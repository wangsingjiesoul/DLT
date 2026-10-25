"""AI HTTP 客户端测试：不访问真实网络，也不消耗模型额度。"""

import json
import unittest
from io import BytesIO
from urllib.error import HTTPError

from dlt.ai import AIConfigError, AISelectionError, OpenAICompatibleSelector


class FakeResponse:
    """模拟 urllib 返回值，让测试可以完全离线运行。"""

    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit=-1):
        return self.body


def api_response(content: str) -> bytes:
    return json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")


class AIClientTests(unittest.TestCase):
    def test_select_calls_chat_completions_and_parses_json(self):
        captured = {}
        model_result = {"selections": [{"strategy": "balanced", "candidate_id": "b-1", "reason": "结构均衡"}]}

        def opener(request, timeout):
            captured["url"] = request.full_url
            captured["authorization"] = request.headers["Authorization"]
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeResponse(api_response(json.dumps(model_result, ensure_ascii=False)))

        selector = OpenAICompatibleSelector(
            base_url="https://example.com/v1",
            api_key="unit-test-secret",
            model="example-model",
            timeout=12,
            opener=opener,
        )
        result = selector.select({"candidates": []})

        self.assertEqual(result, model_result)
        self.assertEqual(captured["url"], "https://example.com/v1/chat/completions")
        self.assertEqual(captured["authorization"], "Bearer unit-test-secret")
        self.assertEqual(captured["payload"]["model"], "example-model")
        self.assertEqual(captured["timeout"], 12)

    def test_markdown_fenced_json_is_accepted(self):
        selector = OpenAICompatibleSelector(
            base_url="https://example.com/v1",
            api_key="unit-test-secret",
            model="example-model",
            opener=lambda *_args, **_kwargs: FakeResponse(api_response('```json\n{"selections": []}\n```')),
        )
        self.assertEqual(selector.select({})["selections"], [])

    def test_invalid_model_json_is_rejected(self):
        selector = OpenAICompatibleSelector(
            base_url="https://example.com/v1",
            api_key="unit-test-secret",
            model="example-model",
            opener=lambda *_args, **_kwargs: FakeResponse(api_response("not-json")),
        )
        with self.assertRaisesRegex(AISelectionError, "有效 JSON"):
            selector.select({})

    def test_http_error_does_not_expose_api_key_or_response_body(self):
        def opener(request, timeout):
            raise HTTPError(
                request.full_url,
                401,
                "unauthorized",
                {},
                BytesIO(b'{"error":"unit-test-secret"}'),
            )

        selector = OpenAICompatibleSelector(
            base_url="https://example.com/v1",
            api_key="unit-test-secret",
            model="example-model",
            opener=opener,
        )
        with self.assertRaisesRegex(AISelectionError, "HTTP 401") as context:
            selector.select({})
        self.assertNotIn("unit-test-secret", str(context.exception))

    def test_timeout_is_reported_without_exposing_api_key(self):
        def opener(_request, timeout):
            raise TimeoutError("unit-test-secret")

        selector = OpenAICompatibleSelector(
            base_url="https://example.com/v1",
            api_key="unit-test-secret",
            model="example-model",
            opener=opener,
        )
        with self.assertRaisesRegex(AISelectionError, "网络请求失败") as context:
            selector.select({})
        self.assertNotIn("unit-test-secret", str(context.exception))

    def test_invalid_base_url_is_rejected(self):
        with self.assertRaises(AIConfigError):
            OpenAICompatibleSelector(
                base_url="file:///tmp/model",
                api_key="unit-test-secret",
                model="example-model",
            )


if __name__ == "__main__":
    unittest.main()
