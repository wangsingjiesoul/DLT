"""使用严格时间顺序，将统计候选与等权随机基线比较。"""

from __future__ import annotations

import hashlib
import random
from statistics import mean
from typing import Any, Callable

from .data import validate_draws
from .predictor import generate_prediction


def _hit_count(combo: dict[str, Any], target: dict[str, Any]) -> tuple[int, int]:
    """返回一组候选分别命中多少个前区和后区号码。"""
    return len(set(combo["front"]) & set(target["front"])), len(set(combo["back"]) & set(target["back"]))


def _best_hits(combos: list[dict[str, Any]], target: dict[str, Any]) -> tuple[int, int]:
    """三组候选中取表现最好的一组，便于与三组随机号码公平比较。"""
    return max((_hit_count(combo, target) for combo in combos), key=lambda pair: (pair[0] + 1.5 * pair[1], pair[0], pair[1]))


def _random_combos(issue: str) -> list[dict[str, list[str]]]:
    """生成可复现的随机基线；相同期号每次都会得到相同三组号码。"""
    seed = int(hashlib.sha256(f"random-baseline:{issue}".encode()).hexdigest()[:16], 16)
    rng = random.Random(seed)
    front_pool = [f"{number:02d}" for number in range(1, 36)]
    back_pool = [f"{number:02d}" for number in range(1, 13)]
    return [
        {"front": sorted(rng.sample(front_pool, 5)), "back": sorted(rng.sample(back_pool, 2))}
        for _ in range(3)
    ]


def run_backtest(
    draws: list[dict[str, Any]],
    *,
    rounds: int = 30,
    candidate_count: int = 250,
    predictor: Callable[..., dict[str, Any]] = generate_prediction,
) -> dict[str, Any]:
    """执行滚动回测：每一期只能看到它之前的历史，防止偷看未来数据。

    外部 AI 默认不会在这里调用，因为远程模型有费用、版本会变化，也难以复现。
    """
    normalized = validate_draws(draws)
    available = min(max(1, rounds), max(1, len(normalized) - 300))
    model_results: list[tuple[int, int]] = []
    random_results: list[tuple[int, int]] = []
    evaluated_issues: list[str] = []
    for index in range(available):
        target = normalized[index]
        # normalized 按新到旧排列，所以 index 后面的记录都早于目标期开奖。
        training = normalized[index + 1 :]
        if len(training) < 300:
            break
        prediction = predictor(training, candidate_count=candidate_count)
        model_results.append(_best_hits(prediction["recommendations"], target))
        random_results.append(_best_hits(_random_combos(target["issue"]), target))
        evaluated_issues.append(target["issue"])

    if not model_results:
        raise ValueError("没有足够历史数据执行回测")

    def summarize(results: list[tuple[int, int]]) -> dict[str, float]:
        return {
            "average_front_hits": round(mean(item[0] for item in results), 4),
            "average_back_hits": round(mean(item[1] for item in results), 4),
            "front_3_plus_rate": round(sum(item[0] >= 3 for item in results) / len(results), 4),
            "back_1_plus_rate": round(sum(item[1] >= 1 for item in results) / len(results), 4),
        }

    return {
        "rounds": len(model_results),
        "newest_issue": evaluated_issues[0],
        "oldest_issue": evaluated_issues[-1],
        "candidate_count_per_strategy": candidate_count,
        "model": summarize(model_results),
        "random_baseline": summarize(random_results),
        "notice": "短期回测波动很大；只有长期、滚动且持续优于随机基线才有参考意义。",
    }
