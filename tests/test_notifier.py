import json
import unittest
from io import BytesIO
from urllib.error import HTTPError

from dlt.notifier import (
    NotificationConfigError,
    PushPlusNotifier,
    QmsgNotifier,
    format_prediction_markdown,
    format_prediction_text,
)


PREDICTION = {
    "target_issue": "26122",
    "target_date": "2026-10-26",
    "based_on_issue": "26121",
    "recommendations": [
        {"strategy": "balanced", "label": "均衡", "front": ["03", "10", "21", "26", "33"], "back": ["01", "07"], "score": 82},
    ],
}


class FakeResponse:
    body = b'{"code":200,"msg":"success"}'

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit=-1):
        return self.body


class FakeQmsgResponse(FakeResponse):
    body = b'{"success":true,"message":"ok","code":0,"data":42}'


class NotifierTests(unittest.TestCase):
    def test_markdown_contains_numbers_and_risk_notice(self):
        text = format_prediction_markdown(PREDICTION)
        self.assertIn("03 10 21 26 33", text)
        self.assertIn("01 07", text)
        self.assertIn("不等同于中奖概率", text)

    def test_plain_text_contains_numbers_without_markdown_headings(self):
        text = format_prediction_text(PREDICTION)
        self.assertIn("03 10 21 26 33", text)
        self.assertIn("26-122", text)
        self.assertNotIn("##", text)

    def test_qmsg_send_builds_json_payload(self):
        captured = {}

        def opener(request, timeout):
            captured["url"] = request.full_url
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeQmsgResponse()

        notifier = QmsgNotifier(key="test-key-123", opener=opener)
        response = notifier.send(PREDICTION)
        self.assertTrue(response["success"])
        self.assertTrue(captured["url"].endswith("/v3/jsend/test-key-123"))
        self.assertIn("03 10 21 26 33", captured["payload"]["msg"])
        self.assertNotIn("group", captured["payload"])

    def test_invalid_qmsg_group_is_rejected(self):
        with self.assertRaises(NotificationConfigError):
            QmsgNotifier(key="test-key-123", group="not-a-group")

    def test_qmsg_http_error_does_not_expose_key(self):
        def opener(request, timeout):
            raise HTTPError(
                request.full_url,
                400,
                "bad request",
                {},
                BytesIO(b'{"success":false,"message":"invalid key"}'),
            )

        notifier = QmsgNotifier(key="secret-key-123", opener=opener)
        with self.assertRaisesRegex(RuntimeError, "Qmsg HTTP 400") as context:
            notifier.send(PREDICTION)
        self.assertNotIn("secret-key-123", str(context.exception))

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

