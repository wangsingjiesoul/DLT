"""回测测试：确认滚动评估会同时给出统计模型和随机基线。"""

import json
import unittest
from pathlib import Path

from dlt.backtest import run_backtest


class BacktestTests(unittest.TestCase):
    def test_walk_forward_backtest_returns_random_baseline(self):
        draws = json.loads(Path("data/dlt.json").read_text(encoding="utf-8"))
        result = run_backtest(draws, rounds=2, candidate_count=100, baseline_repeats=3)
        self.assertEqual(result["rounds"], 2)
        self.assertEqual(result["model_version"], "evidence-calibrated-ensemble-v2")
        self.assertIn("model", result)
        self.assertIn("random_baseline", result)
        self.assertIn("average_front_hits", result["model"])
        self.assertIn("paired_ci95", result["evidence"])
        self.assertIn(result["evidence"]["verdict"], {"historical_outperformance", "historical_underperformance", "inconclusive"})


if __name__ == "__main__":
    unittest.main()
