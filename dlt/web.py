"""不依赖第三方框架的只读 Web 页面与 JSON API。"""

from __future__ import annotations

import json
import math
import mimetypes
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlsplit

from .data import load_draws, write_json_atomic
from .predictor import generate_prediction, prediction_profile


# 默认值对应“未启用 AI”的统计模式；主程序启用 AI 后会显式传入另一个标识。
DEFAULT_PREDICTION_PROFILE = prediction_profile()
DEFAULT_AI_FALLBACK_RETRY_SECONDS = 600

# ThreadingHTTPServer 会并发处理请求。用一把进程内锁避免缓存未生成时重复调用付费 AI。
_PREDICTION_LOCK = threading.Lock()


def security_headers() -> list[tuple[str, str]]:
    """返回每个响应都要携带的浏览器安全头。"""
    return [
        ("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'none'"),
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
        ("Referrer-Policy", "no-referrer"),
        ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
        ("Cache-Control", "no-store"),
    ]


def resolve_static_path(web_root: Path, request_path: str) -> Path | None:
    """把 URL 转成静态文件路径，并阻止 ../ 逃出 web 目录。"""
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


def health_payload(draws: list[dict[str, Any]], service_status: dict[str, bool] | None = None) -> dict[str, Any]:
    """组合数据健康信息和主程序注入的非敏感运行模式。"""
    value: dict[str, Any] = {"status": "ok", "draw_count": len(draws), "latest_issue": draws[0]["issue"]}
    value.update(service_status or {})
    return value


def history_page(draws: list[dict[str, Any]], *, page: int, page_size: int) -> dict[str, Any]:
    """返回历史数据的一页；调用方负责先把页码限制在安全范围内。"""
    if page < 1:
        raise ValueError("page must be at least 1")
    if not 1 <= page_size <= 100:
        raise ValueError("page_size must be between 1 and 100")
    total = len(draws)
    total_pages = max(1, math.ceil(total / page_size))
    start = (page - 1) * page_size
    return {
        "items": draws[start : start + page_size],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
    }


def _read_prediction(
    data_path: Path,
    runtime_dir: Path,
    *,
    predict_func: Callable[[list[dict[str, Any]]], dict[str, Any]] = generate_prediction,
    expected_profile: str = DEFAULT_PREDICTION_PROFILE,
    fallback_retry_seconds: int = DEFAULT_AI_FALLBACK_RETRY_SECONDS,
) -> dict[str, Any]:
    """优先读取同一期、同一模型配置的缓存，否则重新生成预测。"""
    if fallback_retry_seconds < 0:
        raise ValueError("fallback_retry_seconds 不能小于 0")

    def cached_value(prediction_path: Path, latest_issue: str) -> dict[str, Any] | None:
        if not prediction_path.exists():
            return None
        try:
            value = json.loads(prediction_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                return None
            # 数据或模型配置变化时不能继续使用旧缓存。
            if value.get("based_on_issue") != latest_issue or value.get("generation_profile") != expected_profile:
                return None
            ai = value.get("ai")
            if isinstance(ai, dict) and ai.get("status") == "fallback":
                # 回退结果只短暂缓存，冷却结束后重新尝试外部 AI。
                age_seconds = max(0.0, time.time() - prediction_path.stat().st_mtime)
                if age_seconds >= fallback_retry_seconds:
                    return None
            return value
        except (OSError, json.JSONDecodeError):
            return None

    draws = load_draws(data_path)
    latest_issue = draws[0]["issue"]
    prediction_path = runtime_dir / "latest_prediction.json"
    value = cached_value(prediction_path, latest_issue)
    if value is not None:
        return value

    with _PREDICTION_LOCK:
        # 等待锁期间另一个请求可能已经生成了缓存，所以进入锁后必须再次检查。
        value = cached_value(prediction_path, latest_issue)
        if value is not None:
            return value
        prediction = predict_func(draws)
        write_json_atomic(prediction_path, prediction)
        return prediction


def create_handler(
    *,
    data_path: Path,
    runtime_dir: Path,
    web_root: Path,
    predict_func: Callable[[list[dict[str, Any]]], dict[str, Any]] = generate_prediction,
    expected_profile: str = DEFAULT_PREDICTION_PROFILE,
    service_status: dict[str, bool] | None = None,
):
    """创建绑定项目路径和预测函数的请求处理器类。"""
    class Handler(BaseHTTPRequestHandler):
        server_version = "DLTService/0.4"
        sys_version = ""

        def version_string(self) -> str:
            """隐藏默认 Python 版本，减少无必要的服务端信息暴露。"""
            return self.server_version

        def log_message(self, format_string: str, *args: Any) -> None:
            print(f"[web] {self.address_string()} {format_string % args}")

        def _headers(self, status: HTTPStatus, content_type: str, length: int) -> None:
            """统一写入状态码、内容类型、长度和安全响应头。"""
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            for key, value in security_headers():
                self.send_header(key, value)
            self.end_headers()

        def _send_bytes(self, body: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK, *, head_only: bool = False) -> None:
            """发送二进制响应；HEAD 请求只返回头，不发送正文。"""
            self._headers(status, content_type, len(body))
            if not head_only:
                self.wfile.write(body)

        def _send_json(self, value: Any, status: HTTPStatus = HTTPStatus.OK, *, head_only: bool = False) -> None:
            """把 Python 对象编码成 UTF-8 JSON 响应。"""
            body = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
            self._send_bytes(body, "application/json; charset=utf-8", status, head_only=head_only)

        def _handle(self, *, head_only: bool = False) -> None:
            """按路径分派健康检查、预测、历史数据和静态资源请求。"""
            parsed = urlsplit(self.path)
            if parsed.path == "/api/health":
                draws = load_draws(data_path)
                self._send_json(health_payload(draws, service_status), head_only=head_only)
                return
            if parsed.path == "/api/prediction":
                self._send_json(
                    _read_prediction(
                        data_path,
                        runtime_dir,
                        predict_func=predict_func,
                        expected_profile=expected_profile,
                    ),
                    head_only=head_only,
                )
                return
            if parsed.path == "/api/history":
                query = parse_qs(parsed.query)
                try:
                    page = int(query.get("page", ["1"])[0])
                    page_size = int(query.get("page_size", ["30"])[0])
                    if page < 1 or not 1 <= page_size <= 100:
                        raise ValueError
                except ValueError:
                    self._send_json(
                        {"error": "page must be >= 1 and page_size must be between 1 and 100"},
                        HTTPStatus.BAD_REQUEST,
                        head_only=head_only,
                    )
                    return
                self._send_json(history_page(load_draws(data_path), page=page, page_size=page_size), head_only=head_only)
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


def serve(
    *,
    host: str,
    port: int,
    data_path: Path,
    runtime_dir: Path,
    web_root: Path,
    predict_func: Callable[[list[dict[str, Any]]], dict[str, Any]] = generate_prediction,
    prediction_profile: str = DEFAULT_PREDICTION_PROFILE,
    service_status: dict[str, bool] | None = None,
) -> None:
    """启动多线程只读 HTTP 服务；预测函数由主程序按 AI 配置注入。"""
    handler = create_handler(
        data_path=data_path,
        runtime_dir=runtime_dir,
        web_root=web_root,
        predict_func=predict_func,
        expected_profile=prediction_profile,
        service_status=service_status,
    )
    server = ThreadingHTTPServer((host, port), handler)
    print(f"DLT service listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

