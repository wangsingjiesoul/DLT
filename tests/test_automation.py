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
        prediction = {"target_issue": "26122", "recommendations": []}
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


if __name__ == "__main__":
    unittest.main()

