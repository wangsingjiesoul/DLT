import tempfile
import unittest
from pathlib import Path

from dlt.web import resolve_static_path, security_headers


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


if __name__ == "__main__":
    unittest.main()
