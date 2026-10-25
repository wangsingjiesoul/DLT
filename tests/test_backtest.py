import json
import unittest
from pathlib import Path

from dlt.backtest import run_backtest


class BacktestTests(unittest.TestCase):
    def test_walk_forward_backtest_returns_random_baseline(self):
        draws = json.loads(Path("data/dlt.json").read_text(encoding="utf-8"))
        result = run_backtest(draws, rounds=2, candidate_count=100)
        self.assertEqual(result["rounds"], 2)
        self.assertIn("model", result)
        self.assertIn("random_baseline", result)
        self.assertIn("average_front_hits", result["model"])


if __name__ == "__main__":
    unittest.main()
