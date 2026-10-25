"""Web 测试：保护路径安全、响应头和不同模型配置的缓存隔离。"""

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from dlt.web import _read_prediction, health_payload, history_page, resolve_static_path, security_headers


class WebTests(unittest.TestCase):
    def test_frontend_contains_paginated_history_controls(self):
        """页面必须保留分页容器，脚本也必须调用独立历史接口。"""
        html = Path("web/index.html").read_text(encoding="utf-8")
        script = Path("web/app.js").read_text(encoding="utf-8")
        for element_id in ("historyRows", "historyPageSize", "historyPrevious", "historyNext", "historyPageInput"):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn("/api/history?", script)

    def test_static_path_cannot_escape_web_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "index.html").write_text("ok", encoding="utf-8")
            self.assertEqual(resolve_static_path(root, "/"), root / "index.html")
            self.assertIsNone(resolve_static_path(root, "/../secret.txt"))

    def test_security_headers_include_csp_and_nosniff(self):
        headers = dict(security_headers())
        self.assertIn("Content-Security-Policy", headers)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_health_payload_includes_only_injected_runtime_status(self):
        value = health_payload(
            [{"issue": "26121"}],
            {"ai_enabled": True, "ai_required": True, "scheduler_enabled": False},
        )
        self.assertEqual(value["latest_issue"], "26121")
        self.assertTrue(value["ai_enabled"])
        self.assertTrue(value["ai_required"])
        self.assertFalse(value["scheduler_enabled"])
        self.assertNotIn("api_key", value)

    def test_history_page_returns_requested_slice_and_metadata(self):
        draws = [{"issue": f"{index:05d}"} for index in range(65, 0, -1)]
        value = history_page(draws, page=2, page_size=30)
        self.assertEqual(value["total"], 65)
        self.assertEqual(value["total_pages"], 3)
        self.assertEqual(value["page"], 2)
        self.assertEqual(len(value["items"]), 30)
        self.assertEqual(value["items"][0]["issue"], "00035")

    def test_history_page_last_and_out_of_range_pages(self):
        draws = [{"issue": f"{index:05d}"} for index in range(1, 66)]
        self.assertEqual(len(history_page(draws, page=3, page_size=30)["items"]), 5)
        self.assertEqual(history_page(draws, page=4, page_size=30)["items"], [])

    def test_history_page_rejects_invalid_parameters(self):
        with self.assertRaises(ValueError):
            history_page([], page=0, page_size=30)
        with self.assertRaises(ValueError):
            history_page([], page=1, page_size=101)

    def test_stale_prediction_cache_is_regenerated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_path = root / "dlt.json"
            runtime_dir = root / "runtime"
            runtime_dir.mkdir()
            draws = json.loads(Path("data/dlt.json").read_text(encoding="utf-8"))
            data_path.write_text(json.dumps(draws, ensure_ascii=False), encoding="utf-8")
            (runtime_dir / "latest_prediction.json").write_text(
                json.dumps({"based_on_issue": "00000", "target_issue": "00001"}),
                encoding="utf-8",
            )

            prediction = _read_prediction(data_path, runtime_dir)

            self.assertEqual(prediction["based_on_issue"], draws[0]["issue"])
            self.assertNotEqual(prediction["target_issue"], "00001")

    def test_changed_ai_profile_regenerates_cache_with_injected_predictor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_path = root / "dlt.json"
            runtime_dir = root / "runtime"
            runtime_dir.mkdir()
            draws = json.loads(Path("data/dlt.json").read_text(encoding="utf-8"))
            data_path.write_text(json.dumps(draws, ensure_ascii=False), encoding="utf-8")
            (runtime_dir / "latest_prediction.json").write_text(
                json.dumps(
                    {
                        "based_on_issue": draws[0]["issue"],
                        "target_issue": "cached",
                        "generation_profile": "old-profile",
                    }
                ),
                encoding="utf-8",
            )
            calls = []

            def predict(_draws):
                calls.append(True)
                return {
                    "based_on_issue": draws[0]["issue"],
                    "target_issue": "new",
                    "generation_profile": "new-profile",
                }

            prediction = _read_prediction(
                data_path,
                runtime_dir,
                predict_func=predict,
                expected_profile="new-profile",
            )

            self.assertEqual(prediction["target_issue"], "new")
            self.assertEqual(len(calls), 1)

    def test_recent_ai_fallback_cache_is_reused_during_cooldown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_path = root / "dlt.json"
            runtime_dir = root / "runtime"
            runtime_dir.mkdir()
            draws = json.loads(Path("data/dlt.json").read_text(encoding="utf-8"))
            data_path.write_text(json.dumps(draws, ensure_ascii=False), encoding="utf-8")
            cached = {
                "based_on_issue": draws[0]["issue"],
                "target_issue": "fallback-cache",
                "generation_profile": "ai-profile",
                "ai": {"status": "fallback"},
            }
            (runtime_dir / "latest_prediction.json").write_text(json.dumps(cached), encoding="utf-8")

            prediction = _read_prediction(
                data_path,
                runtime_dir,
                predict_func=lambda _draws: self.fail("冷却期内不应重新调用 AI"),
                expected_profile="ai-profile",
                fallback_retry_seconds=600,
            )

            self.assertEqual(prediction["target_issue"], "fallback-cache")

    def test_expired_ai_fallback_cache_retries_prediction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_path = root / "dlt.json"
            runtime_dir = root / "runtime"
            runtime_dir.mkdir()
            draws = json.loads(Path("data/dlt.json").read_text(encoding="utf-8"))
            data_path.write_text(json.dumps(draws, ensure_ascii=False), encoding="utf-8")
            prediction_path = runtime_dir / "latest_prediction.json"
            prediction_path.write_text(
                json.dumps(
                    {
                        "based_on_issue": draws[0]["issue"],
                        "target_issue": "expired-fallback",
                        "generation_profile": "ai-profile",
                        "ai": {"status": "fallback"},
                    }
                ),
                encoding="utf-8",
            )
            expired = time.time() - 700
            os.utime(prediction_path, (expired, expired))

            prediction = _read_prediction(
                data_path,
                runtime_dir,
                predict_func=lambda _draws: {
                    "based_on_issue": draws[0]["issue"],
                    "target_issue": "retried",
                    "generation_profile": "ai-profile",
                    "ai": {"status": "selected"},
                },
                expected_profile="ai-profile",
                fallback_retry_seconds=600,
            )

            self.assertEqual(prediction["target_issue"], "retried")

    def test_concurrent_cache_miss_only_generates_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_path = root / "dlt.json"
            runtime_dir = root / "runtime"
            runtime_dir.mkdir()
            draws = json.loads(Path("data/dlt.json").read_text(encoding="utf-8"))
            data_path.write_text(json.dumps(draws, ensure_ascii=False), encoding="utf-8")
            calls = []

            def predict(_draws):
                calls.append(True)
                time.sleep(0.05)
                return {
                    "based_on_issue": draws[0]["issue"],
                    "target_issue": "single-call",
                    "generation_profile": "concurrent-profile",
                    "ai": {"status": "selected"},
                }

            results = []

            def read():
                results.append(
                    _read_prediction(
                        data_path,
                        runtime_dir,
                        predict_func=predict,
                        expected_profile="concurrent-profile",
                    )
                )

            threads = [threading.Thread(target=read) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(len(calls), 1)
            self.assertEqual(len(results), 4)
            self.assertTrue(all(item["target_issue"] == "single-call" for item in results))


if __name__ == "__main__":
    unittest.main()
