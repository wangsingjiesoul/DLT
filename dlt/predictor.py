"""基于历史统计生成可解释、可复现的大乐透候选组合。"""

from __future__ import annotations

import hashlib
import math
import random
from collections import Counter
from datetime import date, timedelta
from statistics import mean
from typing import Any

from .data import validate_draws


MODEL_VERSION = "statistical-ensemble-v1"
DRAW_WEEKDAYS = {0, 2, 5}  # 周一、周三、周六


def next_draw_identity(latest_issue: str, latest_date: str) -> tuple[str, str]:
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
    low, high = min(values.values()), max(values.values())
    if math.isclose(low, high):
        return {key: 0.5 for key in values}
    return {key: (value - low) / (high - low) for key, value in values.items()}


def _area_features(draws: list[dict[str, Any]], area: str, maximum: int) -> dict[str, dict[str, float]]:
    numbers = [f"{number:02d}" for number in range(1, maximum + 1)]
    frequency: dict[int, dict[str, float]] = {}
    for window in (30, 100, 300):
        counter = Counter(number for draw in draws[:window] for number in draw[area])
        frequency[window] = _minmax({number: float(counter[number]) for number in numbers})

    gaps = {
        number: float(next((index for index, draw in enumerate(draws) if number in draw[area]), len(draws)))
        for number in numbers
    }
    gap_score = _minmax({number: min(value, 40.0) for number, value in gaps.items()})
    decay = {}
    for number in numbers:
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
    available = list(scores)
    result: list[str] = []
    for _ in range(count):
        weights = [scores[number] for number in available]
        selected = rng.choices(available, weights=weights, k=1)[0]
        result.append(selected)
        available.remove(selected)
    return sorted(result)


def _structure_score(front: list[str], back: list[str]) -> float:
    front_values = [int(number) for number in front]
    back_values = [int(number) for number in back]
    zones = [sum(1 <= number <= 12 for number in front_values), sum(13 <= number <= 24 for number in front_values), sum(25 <= number <= 35 for number in front_values)]
    zone_score = 1.0 if all(zones) else 0.45
    odd_count = sum(number % 2 for number in front_values)
    odd_score = 1.0 if odd_count in (2, 3) else 0.65 if odd_count in (1, 4) else 0.2
    total = sum(front_values)
    sum_score = max(0.15, 1 - abs(total - 90) / 55)
    span = max(front_values) - min(front_values)
    span_score = max(0.2, 1 - abs(span - 25) / 22)
    consecutive = sum(right - left == 1 for left, right in zip(front_values, front_values[1:]))
    consecutive_score = 1.0 if consecutive <= 1 else 0.55
    back_score = 1.0 if abs(back_values[1] - back_values[0]) >= 3 else 0.75
    return mean((zone_score, odd_score, sum_score, span_score, consecutive_score, back_score))


def _candidate_pool(
    strategy: str,
    target_issue: str,
    front_features: dict[str, dict[str, float]],
    back_features: dict[str, dict[str, float]],
    candidate_count: int,
) -> list[dict[str, Any]]:
    seed = int(hashlib.sha256(f"{MODEL_VERSION}:{target_issue}:{strategy}".encode()).hexdigest()[:16], 16)
    rng = random.Random(seed)
    front_scores = _strategy_scores(front_features, strategy)
    back_scores = _strategy_scores(back_features, strategy)
    candidates: dict[tuple[str, ...], dict[str, Any]] = {}
    for _ in range(max(candidate_count, 100)):
        front = _weighted_sample(rng, front_scores, 5)
        back = _weighted_sample(rng, back_scores, 2)
        key = tuple(front + back)
        structure = _structure_score(front, back)
        number_score = mean([front_scores[number] for number in front] + [back_scores[number] for number in back])
        raw_score = number_score * 0.68 + structure * 0.32
        candidates[key] = {"front": front, "back": back, "raw_score": raw_score, "structure": structure}
    return sorted(candidates.values(), key=lambda item: item["raw_score"], reverse=True)


def _select_diverse(pool: list[dict[str, Any]], selected: list[dict[str, Any]]) -> dict[str, Any]:
    def adjusted(item: dict[str, Any]) -> float:
        penalty = 0.0
        for previous in selected:
            penalty += 0.055 * len(set(item["front"]) & set(previous["front"]))
            penalty += 0.045 * len(set(item["back"]) & set(previous["back"]))
        return item["raw_score"] - penalty

    return max(pool[: max(80, len(pool) // 5)], key=adjusted)


def _factor_scores(item: dict[str, Any], front_features: dict[str, dict[str, float]], back_features: dict[str, dict[str, float]], selected: list[dict[str, Any]]) -> dict[str, int]:
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


def generate_prediction(draws: list[dict[str, Any]], *, candidate_count: int = 5000) -> dict[str, Any]:
    normalized = validate_draws(draws)
    latest = normalized[0]
    target_issue, target_date = next_draw_identity(latest["issue"], latest["date"])
    front_features = _area_features(normalized, "front", 35)
    back_features = _area_features(normalized, "back", 12)
    selected: list[dict[str, Any]] = []
    recommendations = []
    labels = {"balanced": "均衡", "trend": "趋势", "explore": "探索"}
    for strategy in ("balanced", "trend", "explore"):
        pool = _candidate_pool(strategy, target_issue, front_features, back_features, candidate_count)
        item = _select_diverse(pool, selected)
        factors = _factor_scores(item, front_features, back_features, selected)
        score = round(0.28 * factors["trend"] + 0.16 * factors["gap"] + 0.34 * factors["structure"] + 0.22 * factors["diversity"])
        recommendation = {
            "strategy": strategy,
            "label": labels[strategy],
            "front": item["front"],
            "back": item["back"],
            "score": score,
            "factors": factors,
        }
        recommendations.append(recommendation)
        selected.append(item)

    front_gaps = sorted(((number, int(item["gap_periods"])) for number, item in front_features.items()), key=lambda pair: pair[1], reverse=True)
    back_gaps = sorted(((number, int(item["gap_periods"])) for number, item in back_features.items()), key=lambda pair: pair[1], reverse=True)
    hot_front = sorted(front_features, key=lambda number: front_features[number]["freq30"], reverse=True)[:3]
    return {
        "model_version": MODEL_VERSION,
        "target_issue": target_issue,
        "target_date": target_date,
        "based_on_issue": latest["issue"],
        "based_on_date": latest["date"],
        "history_count": len(normalized),
        "recommendations": recommendations,
        "signals": {
            "hot_front_30": hot_front,
            "front_largest_gap": {"number": front_gaps[0][0], "periods": front_gaps[0][1]},
            "back_largest_gap": {"number": back_gaps[0][0], "periods": back_gaps[0][1]},
        },
        "risk_notice": "开奖结果具有独立随机性，模型评分不等同于中奖概率。",
    }

