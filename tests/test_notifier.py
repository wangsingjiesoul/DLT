import json
import unittest

from dlt.notifier import NotificationConfigError, PushPlusNotifier, format_prediction_markdown


PREDICTION = {
    "target_issue": "26122",
    "target_date": "2026-10-26",
    "based_on_issue": "26121",
    "recommendations": [
        {"strategy": "balanced", "label": "均衡", "front": ["03", "10", "21", "26", "33"], "back": ["01", "07"], "score": 82},
    ],
}


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit=-1):
        return b'{"code":200,"msg":"success"}'


class NotifierTests(unittest.TestCase):
    def test_markdown_contains_numbers_and_risk_notice(self):
        text = format_prediction_markdown(PREDICTION)
        self.assertIn("03 10 21 26 33", text)
        self.assertIn("01 07", text)
        self.assertIn("不等同于中奖概率", text)

    def test_missing_token_is_rejected(self):
        with self.assertRaises(NotificationConfigError):
            PushPlusNotifier(token="")

    def test_send_builds_pushplus_json_payload(self):
        captured = {}

        def opener(request, timeout):
            captured["url"] = request.full_url
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeResponse()

        notifier = PushPlusNotifier(
            token="test-token",
            channel="qq",
            option="qq-config-code",
            opener=opener,
        )
        response = notifier.send(PREDICTION)
        self.assertEqual(response["code"], 200)
        self.assertEqual(captured["payload"]["token"], "test-token")
        self.assertEqual(captured["payload"]["channel"], "qq")
        self.assertNotIn("to", captured["payload"])
        self.assertEqual(captured["payload"]["option"], "qq-config-code")
        self.assertNotIn("test-token", format_prediction_markdown(PREDICTION))

    def test_qq_group_option_rejects_friend_or_topic_target(self):
        with self.assertRaises(NotificationConfigError):
            PushPlusNotifier(
                token="test-token",
                channel="qq",
                recipient="friend-token",
                option="qq-group-code",
            )


if __name__ == "__main__":
    unittest.main()

