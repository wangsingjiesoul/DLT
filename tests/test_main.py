"""主程序配置测试：环境变量必须被正确转换为通知器和 AI 客户端。"""

import os
import unittest
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


if __name__ == "__main__":
    unittest.main()
