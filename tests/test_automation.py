import tempfile
import unittest
from pathlib import Path

from dlt.automation import AutomationRunner


class FakeNotifier:
    def __init__(self):
        self.calls = 0

    def send(self, _prediction):
        self.calls += 1
        return {"code": 200}


class AutomationTests(unittest.TestCase):
    def test_same_target_issue_is_not_sent_twice(self):
        prediction = {"target_issue": "26122", "based_on_date": "2026-10-24", "recommendations": []}
        notifier = FakeNotifier()
        with tempfile.TemporaryDirectory() as tmp:
            runner = AutomationRunner(
                data_path=Path(tmp) / "dlt.json",
                runtime_dir=Path(tmp) / "runtime",
                update_func=lambda *_args, **_kwargs: [],
                predict_func=lambda _draws: prediction,
                notifier=notifier,
                load_func=lambda _path: [{"issue": "26121"}],
            )
            first = runner.run(update=False, notify=True)
            second = runner.run(update=False, notify=True)
        self.assertTrue(first["notified"])
        self.assertFalse(second["notified"])
        self.assertEqual(notifier.calls, 1)

    def test_stale_draw_date_is_not_notified(self):
        notifier = FakeNotifier()
        with tempfile.TemporaryDirectory() as tmp:
            runner = AutomationRunner(
                data_path=Path(tmp) / "dlt.json",
                runtime_dir=Path(tmp) / "runtime",
                update_func=lambda *_args, **_kwargs: [],
                predict_func=lambda _draws: {"target_issue": "26122", "based_on_date": "2026-10-24", "recommendations": []},
                notifier=notifier,
                load_func=lambda _path: [{"issue": "26121"}],
            )
            result = runner.run(update=False, notify=True, expected_latest_date="2026-10-26")
        self.assertFalse(result["fresh"])
        self.assertFalse(result["notified"])
        self.assertEqual(notifier.calls, 0)


if __name__ == "__main__":
    unittest.main()

