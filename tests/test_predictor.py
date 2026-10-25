import json
import unittest
from pathlib import Path

from dlt.predictor import generate_prediction, next_draw_identity


class PredictorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.draws = json.loads(Path("data/dlt.json").read_text(encoding="utf-8"))

    def test_next_draw_identity_rolls_issue_at_new_year(self):
        issue, draw_date = next_draw_identity("26150", "2026-12-30")
        self.assertEqual(issue, "27001")
        self.assertEqual(draw_date, "2027-01-02")

    def test_prediction_is_deterministic_and_valid(self):
        first = generate_prediction(self.draws, candidate_count=600)
        second = generate_prediction(self.draws, candidate_count=600)
        self.assertEqual(first, second)
        self.assertEqual(len(first["recommendations"]), 3)
        self.assertEqual({x["strategy"] for x in first["recommendations"]}, {"balanced", "trend", "explore"})

        combos = set()
        for item in first["recommendations"]:
            self.assertEqual(len(item["front"]), 5)
            self.assertEqual(len(item["back"]), 2)
            self.assertEqual(item["front"], sorted(set(item["front"])))
            self.assertEqual(item["back"], sorted(set(item["back"])))
            self.assertTrue(all(1 <= int(number) <= 35 for number in item["front"]))
            self.assertTrue(all(1 <= int(number) <= 12 for number in item["back"]))
            combos.add(tuple(item["front"] + item["back"]))
        self.assertEqual(len(combos), 3)


if __name__ == "__main__":
    unittest.main()

