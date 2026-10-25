"""基于历史统计生成可解释、可复现的大乐透候选组合。"""

from __future__ import annotations

import hashlib
import math
import random
from collections import Counter
from datetime import date, timedelta
from statistics import mean, pstdev
from typing import Any

from .ai import AISelectionError
from .data import validate_draws


# v2 用真实历史分布校准组合结构，并把更完整的统计摘要交给 AI 审阅。
MODEL_VERSION = "evidence-calibrated-ensemble-v2"
AI_RERANK_VERSION = "candidate-rerank-v2"
AI_CANDIDATES_PER_STRATEGY = 18
DRAW_WEEKDAYS = {0, 2, 5}  # 周一、周三、周六


def next_draw_identity(latest_issue: str, latest_date: str) -> tuple[str, str]:
    """根据最近期开奖日推算下一开奖日，并在跨年时把期号重置为 001。"""
    current_date = date.fromisoformat(latest_date)
    next_date = current_date + timedelta(days=1)
    while next_date.weekday() not in DRAW_WEEKDAYS:
        next_date += timedelta(days=1)
    if next_date.year != current_date.year:
        next_issue = f"{next_date.year % 100:02d}001"
    else:
        next_issue = f"{int(latest_issue) + 1:05d}"
    return next_issue, next_date.isoformat()


def _minmax(values: dict[str, float]) -> dict[str, float]:
    """把一组不同量纲的数值线性缩放到 0～1，便于加权。"""
    low, high = min(values.values()), max(values.values())
    if math.isclose(low, high):
        return {key: 0.5 for key in values}
    return {key: (value - low) / (high - low) for key, value in values.items()}


def _area_features(draws: list[dict[str, Any]], area: str, maximum: int) -> dict[str, dict[str, float]]:
    """为前区或后区每个号码计算频率、遗漏和时间衰减热度。"""
    numbers = [f"{number:02d}" for number in range(1, maximum + 1)]
    frequency: dict[int, dict[str, float]] = {}
    for window in (30, 100, 300):
        # 三个窗口分别观察短期、中期和较长期分布。
        counter = Counter(number for draw in draws[:window] for number in draw[area])
        frequency[window] = _minmax({number: float(counter[number]) for number in numbers})

    gaps = {
        number: float(next((index for index, draw in enumerate(draws) if number in draw[area]), len(draws)))
        for number in numbers
    }
    gap_score = _minmax({number: min(value, 40.0) for number, value in gaps.items()})
    decay = {}
    for number in numbers:
        # 越新的开奖权重越大；55 是平滑速度，不代表任何概率定律。
        decay[number] = sum(math.exp(-index / 55.0) for index, draw in enumerate(draws[:300]) if number in draw[area])
    decay_score = _minmax(decay)

    return {
        number: {
            "freq30": frequency[30][number],
            "freq100": frequency[100][number],
            "freq300": frequency[300][number],
            "gap": gap_score[number],
            "gap_periods": gaps[number],
            "decay": decay_score[number],
        }
        for number in numbers
    }


def _strategy_scores(features: dict[str, dict[str, float]], strategy: str) -> dict[str, float]:
    """按策略给单个号码打权重；权重只用于抽样，不是中奖概率。"""
    scores = {}
    for number, item in features.items():
        if strategy == "trend":
            score = 0.46 * item["freq30"] + 0.32 * item["freq100"] + 0.22 * item["decay"]
        elif strategy == "explore":
            score = 0.44 * item["gap"] + 0.24 * item["freq300"] + 0.18 * item["decay"] + 0.14 * (1 - item["freq30"])
        else:
            score = (
                0.22 * item["freq30"]
                + 0.23 * item["freq100"]
                + 0.20 * item["freq300"]
                + 0.23 * item["decay"]
                + 0.12 * item["gap"]
            )
        scores[number] = max(score, 0.001)
    return scores


def _weighted_sample(rng: random.Random, scores: dict[str, float], count: int) -> list[str]:
    """无放回加权抽样：一个号码在同一区域内最多出现一次。"""
    available = list(scores)
    result: list[str] = []
    for _ in range(count):
        weights = [scores[number] for number in available]
        selected = rng.choices(available, weights=weights, k=1)[0]
        result.append(selected)
        available.remove(selected)
    return sorted(result)


def _structure_values(front: list[str], back: list[str]) -> dict[str, Any]:
    """把一注号码转换成可与历史开奖比较的组合结构。"""
    front_values = [int(number) for number in front]
    back_values = [int(number) for number in back]
    return {
        "front_sum": sum(front_values),
        "front_span": max(front_values) - min(front_values),
        "front_odd_count": sum(number % 2 for number in front_values),
        "front_zones": (
            sum(1 <= number <= 12 for number in front_values),
            sum(13 <= number <= 24 for number in front_values),
            sum(25 <= number <= 35 for number in front_values),
        ),
        "front_consecutive_pairs": sum(
            right - left == 1 for left, right in zip(front_values, front_values[1:])
        ),
        "back_gap": abs(back_values[1] - back_values[0]),
    }


def _structure_profile(draws: list[dict[str, Any]], limit: int = 1_000) -> dict[str, Any]:
    """从最近历史中学习常见结构，替代写死的和值、跨度和奇偶经验值。"""
    rows = [_structure_values(draw["front"], draw["back"]) for draw in draws[:limit]]
    numeric_keys = ("front_sum", "front_span", "back_gap")
    categorical_keys = ("front_odd_count", "front_zones", "front_consecutive_pairs")
    return {
        "sample_size": len(rows),
        "numeric": {
            key: {
                "mean": mean(float(row[key]) for row in rows),
                "stdev": max(1.0, pstdev(float(row[key]) for row in rows)),
            }
            for key in numeric_keys
        },
        "categorical": {key: Counter(row[key] for row in rows) for key in categorical_keys},
    }


def _structure_score(front: list[str], back: list[str], profile: dict[str, Any]) -> float:
    """按历史中结构的常见程度评分；它用于覆盖设计，不代表单注概率更高。"""
    values = _structure_values(front, back)
    scores: list[float] = []
    for key, summary in profile["numeric"].items():
        z_score = (float(values[key]) - summary["mean"]) / summary["stdev"]
        scores.append(math.exp(-0.5 * z_score * z_score))
    for key, counter in profile["categorical"].items():
        # 加 1 平滑避免历史中暂未出现的结构直接得到 0 分。
        scores.append((counter[values[key]] + 1) / (max(counter.values()) + 1))
    return mean(scores)


def _structure_summary(profile: dict[str, Any]) -> dict[str, Any]:
    """生成可序列化摘要，供 AI 和页面解释使用，不暴露整份原始历史。"""
    categorical = profile["categorical"]
    zone_mode = categorical["front_zones"].most_common(1)[0][0]
    return {
        "sample_size": profile["sample_size"],
        "front_sum_mean": round(profile["numeric"]["front_sum"]["mean"], 2),
        "front_sum_stdev": round(profile["numeric"]["front_sum"]["stdev"], 2),
        "front_span_mean": round(profile["numeric"]["front_span"]["mean"], 2),
        "back_gap_mean": round(profile["numeric"]["back_gap"]["mean"], 2),
        "common_odd_count": categorical["front_odd_count"].most_common(1)[0][0],
        "common_zone_pattern": list(zone_mode),
        "common_consecutive_pairs": categorical["front_consecutive_pairs"].most_common(1)[0][0],
    }


def _candidate_pool(
    strategy: str,
    target_issue: str,
    front_features: dict[str, dict[str, float]],
    back_features: dict[str, dict[str, float]],
    structure_profile: dict[str, Any],
    candidate_count: int,
) -> list[dict[str, Any]]:
    """用固定种子生成可复现候选池，再按号码权重和结构分排序。"""
    # 固定种子让同一期、同一版本、同一策略的结果可复现，方便测试和排错。
    seed = int(hashlib.sha256(f"{MODEL_VERSION}:{target_issue}:{strategy}".encode()).hexdigest()[:16], 16)
    rng = random.Random(seed)
    front_scores = _strategy_scores(front_features, strategy)
    back_scores = _strategy_scores(back_features, strategy)
    candidates: dict[tuple[str, ...], dict[str, Any]] = {}
    for _ in range(max(candidate_count, 100)):
        front = _weighted_sample(rng, front_scores, 5)
        back = _weighted_sample(rng, back_scores, 2)
        key = tuple(front + back)
        structure = _structure_score(front, back, structure_profile)
        number_score = mean([front_scores[number] for number in front] + [back_scores[number] for number in back])
        raw_score = number_score * 0.68 + structure * 0.32
        candidates[key] = {
            "front": front,
            "back": back,
            "raw_score": raw_score,
            "structure": structure,
            "structure_values": _structure_values(front, back),
        }
    return sorted(candidates.values(), key=lambda item: item["raw_score"], reverse=True)


def _candidate_id(strategy: str, item: dict[str, Any]) -> str:
    """用策略和号码生成稳定 ID；AI 只能返回这个 ID，不能直接返回号码。"""
    numbers = ",".join(item["front"] + item["back"])
    digest = hashlib.sha256(f"{strategy}:{numbers}".encode("utf-8")).hexdigest()[:12]
    return f"{strategy}-{digest}"


def _select_diverse(pool: list[dict[str, Any]], selected: list[dict[str, Any]]) -> dict[str, Any]:
    """在高分候选中降低号码重合，避免三组结果几乎一样。"""
    def adjusted(item: dict[str, Any]) -> float:
        penalty = 0.0
        for previous in selected:
            penalty += 0.055 * len(set(item["front"]) & set(previous["front"]))
            penalty += 0.045 * len(set(item["back"]) & set(previous["back"]))
        return item["raw_score"] - penalty

    return max(pool[: max(80, len(pool) // 5)], key=adjusted)


def _factor_scores(item: dict[str, Any], front_features: dict[str, dict[str, float]], back_features: dict[str, dict[str, float]], selected: list[dict[str, Any]]) -> dict[str, int]:
    """把内部浮点特征转换为页面展示使用的 0～100 整数分。"""
    features = [front_features[number] for number in item["front"]] + [back_features[number] for number in item["back"]]
    trend = round(100 * mean((feature["freq30"] + feature["freq100"] + feature["decay"]) / 3 for feature in features))
    gap = round(100 * mean(feature["gap"] for feature in features))
    structure = round(100 * item["structure"])
    if not selected:
        diversity = 100
    else:
        overlap = max(
            len(set(item["front"]) & set(previous["front"])) + len(set(item["back"]) & set(previous["back"]))
            for previous in selected
        )
        diversity = max(0, 100 - overlap * 16)
    return {"trend": trend, "gap": gap, "structure": structure, "diversity": diversity}


def _ai_request(
    *,
    target_issue: str,
    target_date: str,
    latest: dict[str, Any],
    history_count: int,
    pools: dict[str, list[dict[str, Any]]],
    front_features: dict[str, dict[str, float]],
    back_features: dict[str, dict[str, float]],
    structure_profile: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, tuple[str, dict[str, Any]]]]:
    """把候选压缩成模型容易理解的 JSON，并保留 ID 到原候选的本地映射。"""
    choices: list[dict[str, Any]] = []
    candidate_lookup: dict[str, tuple[str, dict[str, Any]]] = {}
    for strategy in ("balanced", "trend", "explore"):
        for rank, item in enumerate(pools[strategy][:AI_CANDIDATES_PER_STRATEGY], start=1):
            candidate_id = _candidate_id(strategy, item)
            factors = _factor_scores(item, front_features, back_features, [])
            candidate_lookup[candidate_id] = (strategy, item)
            choices.append(
                {
                    "candidate_id": candidate_id,
                    "strategy": strategy,
                    "rank": rank,
                    "front": item["front"],
                    "back": item["back"],
                    "statistical_score": round(item["raw_score"] * 100, 2),
                    "factors": {key: factors[key] for key in ("trend", "gap", "structure")},
                    "structure": {
                        key: list(value) if isinstance(value, tuple) else value
                        for key, value in item["structure_values"].items()
                    },
                }
            )
    return (
        {
            "task": "从每种策略的候选 ID 中各选择一组；不得创造或修改号码",
            "target_issue": target_issue,
            "target_date": target_date,
            "based_on_issue": latest["issue"],
            "based_on_date": latest["date"],
            "history_count": history_count,
            "history_method": {
                "frequency_windows": [30, 100, 300],
                "recency_decay_window": 300,
                "empirical_structure": _structure_summary(structure_profile),
            },
            "criteria": ["统计评分", "历史结构常见度", "三组号码覆盖差异"],
            "scientific_limit": "所有合法单注理论概率相同；历史指标仅用于候选排序与覆盖设计",
            "candidates": choices,
        },
        candidate_lookup,
    )


def _clean_reason(value: Any) -> str:
    """压缩模型理由，防止控制字符或超长文本进入网页和消息。"""
    if not isinstance(value, str):
        raise AISelectionError("AI 选择理由必须是文本")
    reason = " ".join(value.split())
    if not reason:
        raise AISelectionError("AI 选择理由不能为空")
    return reason[:160]


def _select_with_ai(
    selector: Any,
    request: dict[str, Any],
    candidate_lookup: dict[str, tuple[str, dict[str, Any]]],
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """调用外部模型，并把自由文本响应收紧为三条合法候选选择。"""
    try:
        response = selector.select(request)
    except AISelectionError:
        raise
    except Exception as exc:
        # 自定义兼容客户端也可能抛出其他异常；统一包装，避免泄漏请求或密钥。
        raise AISelectionError("AI 候选重排失败") from exc
    selections = response.get("selections") if isinstance(response, dict) else None
    if not isinstance(selections, list) or len(selections) != 3:
        raise AISelectionError("AI 必须为三种策略各返回一个选择")

    selected: dict[str, dict[str, Any]] = {}
    reasons: dict[str, str] = {}
    selected_combinations: set[tuple[str, ...]] = set()
    expected_strategies = {"balanced", "trend", "explore"}
    for value in selections:
        if not isinstance(value, dict):
            raise AISelectionError("AI 选择项必须是 JSON 对象")
        strategy = value.get("strategy")
        candidate_id = value.get("candidate_id")
        if strategy not in expected_strategies or strategy in selected:
            raise AISelectionError("AI 返回了缺失、重复或未知的策略")
        if not isinstance(candidate_id, str) or candidate_id not in candidate_lookup:
            raise AISelectionError("AI 返回了候选池之外的 ID")
        candidate_strategy, item = candidate_lookup[candidate_id]
        if candidate_strategy != strategy:
            raise AISelectionError("AI 候选 ID 与策略不匹配")
        combination = tuple(item["front"] + item["back"])
        if combination in selected_combinations:
            raise AISelectionError("AI 为不同策略选择了重复号码")
        selected[strategy] = item
        reasons[strategy] = _clean_reason(value.get("reason"))
        selected_combinations.add(combination)
    if set(selected) != expected_strategies:
        raise AISelectionError("AI 未完整选择三种策略")
    return selected, reasons


def _statistical_selection(pools: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    """沿用原来的顺序选择，并逐步惩罚三组之间的号码重合。"""
    chosen: dict[str, dict[str, Any]] = {}
    selected_items: list[dict[str, Any]] = []
    for strategy in ("balanced", "trend", "explore"):
        item = _select_diverse(pools[strategy], selected_items)
        chosen[strategy] = item
        selected_items.append(item)
    return chosen


def prediction_profile(ai_selector: Any | None = None, ai_required: bool = False) -> str:
    """返回缓存标识；AI 模型或严格模式变化时，Web 会重新生成结果。"""
    if ai_selector is None:
        return f"{MODEL_VERSION}:statistical"
    selector_profile = str(getattr(ai_selector, "profile_id", "custom"))[:80]
    strictness = "required" if ai_required else "fallback"
    return f"{MODEL_VERSION}:{AI_RERANK_VERSION}:{selector_profile}:{strictness}"


def generate_prediction(
    draws: list[dict[str, Any]],
    *,
    candidate_count: int = 5000,
    ai_selector: Any | None = None,
    ai_required: bool = False,
) -> dict[str, Any]:
    """生成三组候选，并在配置后让外部 AI 从受控候选池中重排。"""
    if ai_required and ai_selector is None:
        raise AISelectionError("AI_REQUIRED 已启用，但没有可用的 AI 模型配置")
    normalized = validate_draws(draws)
    latest = normalized[0]
    target_issue, target_date = next_draw_identity(latest["issue"], latest["date"])
    front_features = _area_features(normalized, "front", 35)
    back_features = _area_features(normalized, "back", 12)
    structure_profile = _structure_profile(normalized)
    pools = {
        strategy: _candidate_pool(
            strategy,
            target_issue,
            front_features,
            back_features,
            structure_profile,
            candidate_count,
        )
        for strategy in ("balanced", "trend", "explore")
    }

    ai_used = False
    ai_error: str | None = None
    reasons: dict[str, str] = {}
    chosen = _statistical_selection(pools)
    if ai_selector is not None:
        request, candidate_lookup = _ai_request(
            target_issue=target_issue,
            target_date=target_date,
            latest=latest,
            history_count=len(normalized),
            pools=pools,
            front_features=front_features,
            back_features=back_features,
            structure_profile=structure_profile,
        )
        try:
            chosen, reasons = _select_with_ai(ai_selector, request, candidate_lookup)
            ai_used = True
        except AISelectionError as exc:
            if ai_required:
                raise
            ai_error = str(exc)

    selected: list[dict[str, Any]] = []
    recommendations: list[dict[str, Any]] = []
    labels = {"balanced": "均衡", "trend": "趋势", "explore": "探索"}
    for strategy in ("balanced", "trend", "explore"):
        item = chosen[strategy]
        candidate_rank = next(index for index, candidate in enumerate(pools[strategy], start=1) if candidate is item)
        factors = _factor_scores(item, front_features, back_features, selected)
        score = round(0.28 * factors["trend"] + 0.16 * factors["gap"] + 0.34 * factors["structure"] + 0.22 * factors["diversity"])
        recommendation = {
            "strategy": strategy,
            "label": labels[strategy],
            "front": item["front"],
            "back": item["back"],
            "score": score,
            "factors": factors,
            "candidate_rank": candidate_rank,
            "selection_source": "ai" if ai_used else "statistics",
        }
        if ai_used:
            recommendation["selection_reason"] = reasons[strategy]
        recommendations.append(recommendation)
        selected.append(item)

    front_gaps = sorted(((number, int(item["gap_periods"])) for number, item in front_features.items()), key=lambda pair: pair[1], reverse=True)
    back_gaps = sorted(((number, int(item["gap_periods"])) for number, item in back_features.items()), key=lambda pair: pair[1], reverse=True)
    hot_front = sorted(front_features, key=lambda number: front_features[number]["freq30"], reverse=True)[:3]
    model_name = str(getattr(ai_selector, "model", ""))[:200] if ai_selector is not None else None
    if ai_selector is None:
        ai_status = "disabled"
        ai_message = "未启用外部 AI，当前使用确定性统计排序"
    elif ai_used:
        ai_status = "selected"
        ai_message = "外部 AI 已从本地合法候选池中完成重排"
    else:
        ai_status = "fallback"
        ai_message = f"外部 AI 未完成重排，已回退统计排序：{ai_error}"
    return {
        "model_version": MODEL_VERSION,
        "generation_profile": prediction_profile(ai_selector, ai_required),
        "target_issue": target_issue,
        "target_date": target_date,
        "based_on_issue": latest["issue"],
        "based_on_date": latest["date"],
        "history_count": len(normalized),
        "methodology": {
            "history_used": len(normalized),
            "frequency_windows": [30, 100, 300],
            "structure_sample_size": structure_profile["sample_size"],
            "structure_summary": _structure_summary(structure_profile),
            "candidate_pool_size": {strategy: len(pool) for strategy, pool in pools.items()},
            "ai_role": "只从本地合法候选 ID 中选择和解释，不允许创造号码",
            "scientific_limit": "历史统计与 AI 都不能改变任意合法单注的理论中奖概率",
        },
        "recommendations": recommendations,
        "ai": {
            "enabled": ai_selector is not None,
            "used": ai_used,
            "required": ai_required,
            "model": model_name,
            "status": ai_status,
            "message": ai_message,
        },
        "signals": {
            "hot_front_30": hot_front,
            "front_largest_gap": {"number": front_gaps[0][0], "periods": front_gaps[0][1]},
            "back_largest_gap": {"number": back_gaps[0][0], "periods": back_gaps[0][1]},
        },
        "risk_notice": "开奖结果具有独立随机性，模型评分不等同于中奖概率。",
    }

