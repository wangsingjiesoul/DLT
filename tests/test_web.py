"""Web 测试：保护路径安全、响应头和不同模型配置的缓存隔离。"""

import json
import tempfile
import unittest
from pathlib import Path

from dlt.web import _read_prediction, resolve_static_path, security_headers


class WebTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
