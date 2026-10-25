"""主程序配置测试：环境变量必须被正确转换为通知器和 AI 客户端。"""

import json
import os
import tempfile
import unittest
from pathlib import Path
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


class MainDoctorTests(unittest.TestCase):
    """部署体检只能读取本地配置，并且输出不得包含任何凭证或接收人。"""

    @staticmethod
    def _data_file(root: Path) -> Path:
        path = root / "dlt.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "issue": "26121",
                        "date": "2026-10-24",
                        "front": ["06", "08", "19", "24", "25"],
                        "back": ["01", "08"],
                    }
                ]
            ),
            encoding="utf-8",
        )
        return path

    @staticmethod
    def _clear_project_environment() -> None:
        """保留 Windows 查找时区所需的系统变量，只移除本项目配置。"""
        prefixes = ("AI_", "QMSG_", "PUSHPLUS_", "DLT_")
        for key in list(os.environ):
            if key == "NOTIFY_PROVIDER" or key.startswith(prefixes):
                os.environ.pop(key, None)

    def test_deployment_ready_when_ai_strict_mode_and_notification_are_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {}, clear=False):
                self._clear_project_environment()
                os.environ.update(
                    {
                    "AI_ENABLED": "true",
                    "AI_REQUIRED": "true",
                    "AI_BASE_URL": "https://example.com/v1",
                    "AI_API_KEY": "unit-test-ai-key",
                    "AI_MODEL": "example-model",
                    "NOTIFY_PROVIDER": "pushplus",
                    "PUSHPLUS_TOKEN": "unit-test-push-token",
                    "PUSHPLUS_CHANNELS": "wechat,qq",
                    "DLT_TIMEZONE": "Asia/Shanghai",
                    }
                )
                report = main.build_doctor_report(data_path=self._data_file(root), runtime_dir=root / "runtime")

        self.assertTrue(report["ok"])
        self.assertTrue(report["deployment_ready"])
        self.assertEqual(report["notifications"]["channels"], ["pushplus:wechat", "pushplus:qq"])
        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("unit-test-ai-key", serialized)
        self.assertNotIn("unit-test-push-token", serialized)
        self.assertNotIn("example.com", serialized)

    def test_missing_ai_and_notifier_are_visible_without_breaking_basic_health(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {}, clear=False):
                self._clear_project_environment()
                report = main.build_doctor_report(data_path=self._data_file(root), runtime_dir=root / "runtime")

        self.assertTrue(report["ok"])
        self.assertFalse(report["deployment_ready"])
        self.assertFalse(report["ai"]["configured"])
        self.assertFalse(report["notifications"]["configured"])

    def test_qmsg_group_number_is_not_exposed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {}, clear=False):
                self._clear_project_environment()
                os.environ.update(
                    {"NOTIFY_PROVIDER": "qmsg", "QMSG_KEY": "unit-test-key", "QMSG_GROUP": "123456789"}
                )
                report = main.build_doctor_report(data_path=self._data_file(root), runtime_dir=root / "runtime")

        self.assertEqual(report["notifications"]["channels"], ["qmsg:group"])
        self.assertNotIn("123456789", json.dumps(report))

    def test_invalid_ai_configuration_uses_generic_safe_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {}, clear=False):
                self._clear_project_environment()
                os.environ.update(
                    {
                    "AI_ENABLED": "true",
                    "AI_BASE_URL": "https://unit-test-secret@example.com/v1",
                    "AI_API_KEY": "unit-test-ai-key",
                    "AI_MODEL": "example-model",
                    }
                )
                report = main.build_doctor_report(data_path=self._data_file(root), runtime_dir=root / "runtime")

        serialized = json.dumps(report, ensure_ascii=False)
        self.assertFalse(report["ok"])
        self.assertEqual(report["ai"]["error"], "AI 配置无效")
        self.assertNotIn("unit-test-secret", serialized)
        self.assertNotIn("unit-test-ai-key", serialized)


if __name__ == "__main__":
    unittest.main()
