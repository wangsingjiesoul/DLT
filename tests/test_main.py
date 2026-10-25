"""主程序配置测试：环境变量必须被正确转换为通知器和 AI 客户端。"""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import main


class MainNotificationConfigTests(unittest.TestCase):
    def test_pushplus_multiple_channels_are_built_once_each(self):
        with patch.dict(
            os.environ,
            {
                "NOTIFY_PROVIDER": "pushplus",
                "PUSHPLUS_TOKEN": "test-token",
                "PUSHPLUS_CHANNELS": "wechat,qq,wechat",
            },
            clear=True,
        ):
            notifiers = main.build_notifiers(required=True)

        self.assertEqual([item.notification_id for item in notifiers], ["pushplus:wechat", "pushplus:qq"])


class MainAIConfigTests(unittest.TestCase):
    def test_ai_is_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            selector, required = main.build_ai_selector()
        self.assertIsNone(selector)
        self.assertFalse(required)

    def test_ai_selector_is_built_from_environment(self):
        with patch.dict(
            os.environ,
            {
                "AI_ENABLED": "true",
                "AI_REQUIRED": "true",
                "AI_BASE_URL": "https://example.com/v1",
                "AI_API_KEY": "unit-test-key",
                "AI_MODEL": "example-model",
                "AI_TIMEOUT": "20",
            },
            clear=True,
        ):
            selector, required = main.build_ai_selector()
        self.assertEqual(selector.model, "example-model")
        self.assertEqual(selector.timeout, 20)
        self.assertTrue(required)

    def test_required_ai_must_also_be_enabled(self):
        with patch.dict(os.environ, {"AI_REQUIRED": "true"}, clear=True):
            with self.assertRaisesRegex(ValueError, "AI_ENABLED=true"):
                main.build_ai_selector()


class MainAIConnectivityCheckTests(unittest.TestCase):
    """检查命令必须真实要求 AI 成功，同时不能触发任何通知逻辑。"""

    def test_ai_check_requires_ai_to_be_enabled(self):
        with patch("main.build_ai_selector", return_value=(None, False)):
            with self.assertRaisesRegex(ValueError, "AI 尚未启用"):
                main.command_ai_check(SimpleNamespace(candidates=300))

    def test_ai_check_forces_strict_mode_and_prints_safe_summary(self):
        selector = object()
        prediction = {
            "target_issue": "26122",
            "recommendations": [{}, {}, {}],
            "ai": {"used": True, "status": "selected", "model": "example-model"},
        }
        with (
            patch("main.build_ai_selector", return_value=(selector, False)),
            patch("main.load_draws", return_value=[{"issue": "26121"}]),
            patch("main.generate_prediction", return_value=prediction) as generate,
            patch("builtins.print") as output,
            patch("main.build_notifiers", side_effect=AssertionError("不应创建通知器")),
        ):
            result = main.command_ai_check(SimpleNamespace(candidates=300))

        self.assertEqual(result, 0)
        self.assertTrue(generate.call_args.kwargs["ai_required"])
        self.assertEqual(generate.call_args.kwargs["candidate_count"], 300)
        printed = output.call_args.args[0]
        self.assertIn('"ok": true', printed)
        self.assertIn("没有发送任何通知", printed)

    def test_ai_check_rejects_invalid_candidate_count(self):
        with patch("main.build_ai_selector", return_value=(object(), True)):
            with self.assertRaisesRegex(ValueError, "100 到 5000"):
                main.command_ai_check(SimpleNamespace(candidates=10))


if __name__ == "__main__":
    unittest.main()
