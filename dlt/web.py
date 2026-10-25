"""不依赖第三方框架的只读 Web 页面与 JSON API。"""

from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from .data import load_draws, write_json_atomic
from .predictor import generate_prediction


def security_headers() -> list[tuple[str, str]]:
    return [
        ("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'none'"),
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
        ("Referrer-Policy", "no-referrer"),
        ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
        ("Cache-Control", "no-store"),
    ]


def resolve_static_path(web_root: Path, request_path: str) -> Path | None:
    relative = unquote(request_path).split("?", 1)[0].lstrip("/") or "index.html"
    candidate = (web_root / relative).resolve()
    root = web_root.resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


def _read_prediction(data_path: Path, runtime_dir: Path) -> dict[str, Any]:
    draws = load_draws(data_path)
    latest_issue = draws[0]["issue"]
    prediction_path = runtime_dir / "latest_prediction.json"
    if prediction_path.exists():
        try:
            value = json.loads(prediction_path.read_text(encoding="utf-8"))
            # 数据由 CLI 单独更新时，不能继续向页面返回基于旧期号的缓存。
            if isinstance(value, dict) and value.get("based_on_issue") == latest_issue:
                return value
        except (OSError, json.JSONDecodeError):
            pass
    prediction = generate_prediction(draws)
    write_json_atomic(prediction_path, prediction)
    return prediction


def create_handler(*, data_path: Path, runtime_dir: Path, web_root: Path):
    class Handler(BaseHTTPRequestHandler):
        server_version = "DLTService/0.2"
        sys_version = ""

        def version_string(self) -> str:
            return self.server_version

        def log_message(self, format_string: str, *args: Any) -> None:
            print(f"[web] {self.address_string()} {format_string % args}")

        def _headers(self, status: HTTPStatus, content_type: str, length: int) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            for key, value in security_headers():
                self.send_header(key, value)
            self.end_headers()

        def _send_bytes(self, body: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK, *, head_only: bool = False) -> None:
            self._headers(status, content_type, len(body))
            if not head_only:
                self.wfile.write(body)

        def _send_json(self, value: Any, status: HTTPStatus = HTTPStatus.OK, *, head_only: bool = False) -> None:
            body = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
            self._send_bytes(body, "application/json; charset=utf-8", status, head_only=head_only)

        def _handle(self, *, head_only: bool = False) -> None:
            parsed = urlsplit(self.path)
            if parsed.path == "/api/health":
                draws = load_draws(data_path)
                self._send_json({"status": "ok", "draw_count": len(draws), "latest_issue": draws[0]["issue"]}, head_only=head_only)
                return
            if parsed.path == "/api/prediction":
                self._send_json(_read_prediction(data_path, runtime_dir), head_only=head_only)
                return
            if parsed.path == "/api/history/latest":
                query = parse_qs(parsed.query)
                try:
                    limit = max(1, min(100, int(query.get("limit", ["10"])[0])))
                except ValueError:
                    self._send_json({"error": "limit must be an integer"}, HTTPStatus.BAD_REQUEST, head_only=head_only)
                    return
                self._send_json(load_draws(data_path)[:limit], head_only=head_only)
                return
            if parsed.path.startswith("/api/"):
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND, head_only=head_only)
                return

            static_path = resolve_static_path(web_root, parsed.path)
            if static_path is None:
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND, head_only=head_only)
                return
            content_type = mimetypes.guess_type(static_path.name)[0] or "application/octet-stream"
            if content_type.startswith("text/") or content_type in {"application/javascript", "image/svg+xml"}:
                content_type += "; charset=utf-8"
            self._send_bytes(static_path.read_bytes(), content_type, head_only=head_only)

        def do_GET(self) -> None:  # noqa: N802
            self._handle()

        def do_HEAD(self) -> None:  # noqa: N802
            self._handle(head_only=True)

        def do_POST(self) -> None:  # noqa: N802
            self._send_json({"error": "method not allowed"}, HTTPStatus.METHOD_NOT_ALLOWED)

    return Handler


def serve(*, host: str, port: int, data_path: Path, runtime_dir: Path, web_root: Path) -> None:
    server = ThreadingHTTPServer((host, port), create_handler(data_path=data_path, runtime_dir=runtime_dir, web_root=web_root))
    print(f"DLT service listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

