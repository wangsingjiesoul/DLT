"""预测器测试：验证确定性、号码合法性以及 AI 只能选择受控候选。"""

import json
import unittest
from pathlib import Path

from dlt.ai import AISelectionError
from dlt.predictor import generate_prediction, next_draw_identity


class FirstValidAISelector:
    """从每种策略中选第一个不重复组合，用来模拟一次成功的 AI 响应。"""

    model = "fake-ai-model"
    profile_id = "fake-profile"

    def select(self, request):
        selections = []
        used = set()
        for strategy in ("balanced", "trend", "explore"):
            for candidate in request["candidates"]:
                combination = tuple(candidate["front"] + candidate["back"])
                if candidate["strategy"] == strategy and combination not in used:
                    selections.append(
                        {
                            "strategy": strategy,
                            "candidate_id": candidate["candidate_id"],
                            "reason": f"{strategy} 测试理由",
                        }
                    )
                    used.add(combination)
                    break
        return {"selections": selections}


class InvalidAISelector:
    model = "fake-ai-model"
    profile_id = "fake-profile"

    def select(self, _request):
        return {
            "selections": [
                {"strategy": strategy, "candidate_id": "not-in-pool", "reason": "非法 ID"}
                for strategy in ("balanced", "trend", "explore")
            ]
        }


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
        self.assertEqual(first["model_version"], "evidence-calibrated-ensemble-v2")
        self.assertEqual(first["methodology"]["history_used"], len(self.draws))
        self.assertEqual(first["methodology"]["frequency_windows"], [30, 100, 300])
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

    def test_ai_can_only_select_candidates_from_the_pool(self):
        prediction = generate_prediction(
            self.draws,
            candidate_count=300,
            ai_selector=FirstValidAISelector(),
            ai_required=True,
        )
        self.assertTrue(prediction["ai"]["used"])
        self.assertEqual(prediction["ai"]["status"], "selected")
        self.assertEqual(prediction["ai"]["model"], "fake-ai-model")
        self.assertTrue(all("selection_reason" in item for item in prediction["recommendations"]))

    def test_invalid_ai_selection_falls_back_when_not_required(self):
        prediction = generate_prediction(
            self.draws,
            candidate_count=300,
            ai_selector=InvalidAISelector(),
            ai_required=False,
        )
        self.assertFalse(prediction["ai"]["used"])
        self.assertEqual(prediction["ai"]["status"], "fallback")
        self.assertNotIn("selection_reason", prediction["recommendations"][0])

    def test_invalid_ai_selection_stops_strict_mode(self):
        with self.assertRaises(AISelectionError):
            generate_prediction(
                self.draws,
                candidate_count=300,
                ai_selector=InvalidAISelector(),
                ai_required=True,
            )

    def test_required_ai_cannot_be_enabled_without_selector(self):
        with self.assertRaises(AISelectionError):
            generate_prediction(self.draws, candidate_count=100, ai_required=True)


if __name__ == "__main__":
    unittest.main()

