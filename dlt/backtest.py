"""使用严格时间顺序，将统计候选与等权随机基线比较。"""

from __future__ import annotations

import hashlib
import math
import random
from statistics import mean, stdev
from typing import Any, Callable

from .data import validate_draws
from .predictor import generate_prediction


def _hit_count(combo: dict[str, Any], target: dict[str, Any]) -> tuple[int, int]:
    """返回一组候选分别命中多少个前区和后区号码。"""
    return len(set(combo["front"]) & set(target["front"])), len(set(combo["back"]) & set(target["back"]))


def _best_hits(combos: list[dict[str, Any]], target: dict[str, Any]) -> tuple[int, int]:
    """三组候选中取表现最好的一组，便于与三组随机号码公平比较。"""
    return max((_hit_count(combo, target) for combo in combos), key=lambda pair: (pair[0] + 1.5 * pair[1], pair[0], pair[1]))


def _random_combos(issue: str, repeat: int = 0) -> list[dict[str, list[str]]]:
    """生成可复现的随机基线；repeat 用于同一期抽取多组独立基线。"""
    seed = int(hashlib.sha256(f"random-baseline:{issue}:{repeat}".encode()).hexdigest()[:16], 16)
    rng = random.Random(seed)
    front_pool = [f"{number:02d}" for number in range(1, 36)]
    back_pool = [f"{number:02d}" for number in range(1, 13)]
    return [
        {"front": sorted(rng.sample(front_pool, 5)), "back": sorted(rng.sample(back_pool, 2))}
        for _ in range(3)
    ]


def _metrics(hits: tuple[float, float]) -> dict[str, float]:
    """把前后区命中数转换为可汇总指标。1.5 只是统一比较用权重。"""
    front, back = hits
    return {
        "front_hits": float(front),
        "back_hits": float(back),
        "front_3_plus": float(front >= 3),
        "back_1_plus": float(back >= 1),
        "utility": float(front + 1.5 * back),
    }


def _average_random_metrics(issue: str, target: dict[str, Any], repeats: int) -> dict[str, float]:
    """同一期重复抽取随机基线并求均值，降低单次随机号码带来的偶然波动。"""
    rows = [_metrics(_best_hits(_random_combos(issue, repeat), target)) for repeat in range(repeats)]
    return {key: mean(row[key] for row in rows) for key in rows[0]}


def _summary(results: list[dict[str, float]]) -> dict[str, float]:
    """汇总模型或随机基线在所有回测期次上的表现。"""
    return {
        "average_front_hits": round(mean(item["front_hits"] for item in results), 4),
        "average_back_hits": round(mean(item["back_hits"] for item in results), 4),
        "front_3_plus_rate": round(mean(item["front_3_plus"] for item in results), 4),
        "back_1_plus_rate": round(mean(item["back_1_plus"] for item in results), 4),
        "average_composite_score": round(mean(item["utility"] for item in results), 4),
    }


def _paired_confidence_interval(
    model_results: list[dict[str, float]],
    random_results: list[dict[str, float]],
    key: str,
) -> dict[str, float]:
    """计算配对均值差和近似 95% 置信区间；区间跨 0 表示证据不足。"""
    differences = [model[key] - baseline[key] for model, baseline in zip(model_results, random_results)]
    average = mean(differences)
    margin = 0.0 if len(differences) < 2 else 1.96 * stdev(differences) / math.sqrt(len(differences))
    return {
        "mean_difference": round(average, 4),
        "ci95_low": round(average - margin, 4),
        "ci95_high": round(average + margin, 4),
    }


def run_backtest(
    draws: list[dict[str, Any]],
    *,
    rounds: int = 30,
    candidate_count: int = 250,
    baseline_repeats: int = 30,
    predictor: Callable[..., dict[str, Any]] = generate_prediction,
) -> dict[str, Any]:
    """执行滚动回测：每一期只能看到它之前的历史，防止偷看未来数据。

    外部 AI 默认不会在这里调用，因为远程模型有费用、版本会变化，也难以复现。
    """
    if not 1 <= baseline_repeats <= 500:
        raise ValueError("baseline_repeats 必须在 1 到 500 之间")
    normalized = validate_draws(draws)
    available = min(max(1, rounds), max(1, len(normalized) - 300))
    model_results: list[dict[str, float]] = []
    random_results: list[dict[str, float]] = []
    evaluated_issues: list[str] = []
    for index in range(available):
        target = normalized[index]
        # normalized 按新到旧排列，所以 index 后面的记录都早于目标期开奖。
        training = normalized[index + 1 :]
        if len(training) < 300:
            break
        prediction = predictor(training, candidate_count=candidate_count)
        model_results.append(_metrics(_best_hits(prediction["recommendations"], target)))
        random_results.append(_average_random_metrics(target["issue"], target, baseline_repeats))
        evaluated_issues.append(target["issue"])

    if not model_results:
        raise ValueError("没有足够历史数据执行回测")

    confidence = {
        "front_hits": _paired_confidence_interval(model_results, random_results, "front_hits"),
        "back_hits": _paired_confidence_interval(model_results, random_results, "back_hits"),
        "composite_score": _paired_confidence_interval(model_results, random_results, "utility"),
    }
    composite = confidence["composite_score"]
    if composite["ci95_low"] > 0:
        verdict = "historical_outperformance"
        verdict_text = "在这段历史样本中优于随机基线，但仍需样本外持续验证。"
    elif composite["ci95_high"] < 0:
        verdict = "historical_underperformance"
        verdict_text = "在这段历史样本中落后于随机基线，不应据此采用当前参数。"
    else:
        verdict = "inconclusive"
        verdict_text = "95% 置信区间跨过 0，尚无足够证据证明优于随机基线。"

    return {
        "model_version": prediction.get("model_version", "unknown"),
        "rounds": len(model_results),
        "newest_issue": evaluated_issues[0],
        "oldest_issue": evaluated_issues[-1],
        "candidate_count_per_strategy": candidate_count,
        "random_baseline_repeats_per_issue": baseline_repeats,
        "model": _summary(model_results),
        "random_baseline": _summary(random_results),
        "evidence": {"verdict": verdict, "message": verdict_text, "paired_ci95": confidence},
        "notice": "回测严格按时间滚动，但模型参数仍可能受同一历史数据影响；只有长期样本外持续领先才有参考意义。",
    }
