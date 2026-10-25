"""从历史开奖表格增量更新大乐透数据。"""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .data import ISSUE_RE, load_draws, validate_draw, validate_draws, write_json_atomic


HISTORY_URL = "https://datachart.500.com/dlt/history/newinc/history.php?start={start}&end=99999"
MAX_RESPONSE_BYTES = 2_000_000


class UpdateError(RuntimeError):
    """外部数据获取或解析失败。"""


def _plain_text(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def parse_history_html(document: str) -> list[dict[str, Any]]:
    """解析 500 历史表格；只接收满足大乐透字段约束的行。"""
    rows: list[dict[str, Any]] = []
    for row_html in re.findall(r"<tr\b[^>]*>(.*?)</tr>", document, flags=re.IGNORECASE | re.DOTALL):
        # 页面在真实数据行前保留了一个注释掉的 <td>，必须先移除注释，
        # 否则正则会把注释内容误判为第一列期号。
        row_html = re.sub(r"<!--.*?-->", "", row_html, flags=re.DOTALL)
        cells = re.findall(r"<td\b([^>]*)>(.*?)</td>", row_html, flags=re.IGNORECASE | re.DOTALL)
        if not cells:
            continue
        values = [_plain_text(content) for _attributes, content in cells]
        if not values or not ISSUE_RE.fullmatch(values[0]):
            continue
        front = [
            _plain_text(content)
            for attributes, content in cells
            if re.search(r'class=["\'][^"\']*\bcfont2\b', attributes, flags=re.IGNORECASE)
        ]
        back = [
            _plain_text(content)
            for attributes, content in cells
            if re.search(r'class=["\'][^"\']*\bcfont4\b', attributes, flags=re.IGNORECASE)
        ]
        draw_date = next((value for value in reversed(values) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)), None)
        if draw_date is None:
            continue
        try:
            rows.append(validate_draw({"issue": values[0], "date": draw_date, "front": front, "back": back}))
        except ValueError:
            continue
    return validate_draws(rows) if rows else []


def merge_draws(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = {draw["issue"]: draw for draw in existing}
    merged.update({draw["issue"]: draw for draw in incoming})
    return validate_draws(list(merged.values()))


def _decode_response(response: Any, payload: bytes) -> str:
    charset = None
    headers = getattr(response, "headers", None)
    if headers is not None and hasattr(headers, "get_content_charset"):
        charset = headers.get_content_charset()
    for encoding in (charset, "gb18030", "utf-8"):
        if not encoding:
            continue
        try:
            return payload.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            pass
    raise UpdateError("无法识别历史数据页面编码")


def fetch_history(start_issue: str, *, opener: Callable[..., Any] = urlopen, timeout: int = 25) -> list[dict[str, Any]]:
    if not ISSUE_RE.fullmatch(start_issue):
        raise UpdateError("起始期号格式无效")
    request = Request(
        HISTORY_URL.format(start=start_issue),
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; DLTDataBot/0.2; +https://github.com/wangsingjiesoul/DLT)",
            "Referer": "https://datachart.500.com/dlt/history/history.shtml",
            "Accept": "text/html,application/xhtml+xml",
        },
        method="GET",
    )
    try:
        with opener(request, timeout=timeout) as response:
            payload = response.read(MAX_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise UpdateError(f"获取历史数据失败: {exc}") from exc
    if len(payload) > MAX_RESPONSE_BYTES:
        raise UpdateError("历史数据响应超过大小限制")
    draws = parse_history_html(_decode_response(response, payload))
    if not draws:
        raise UpdateError("历史数据页面中没有解析到有效大乐透记录")
    return draws


def update_data(path: str | Path, *, opener: Callable[..., Any] = urlopen) -> dict[str, Any]:
    data_path = Path(path)
    existing = load_draws(data_path)
    incoming = fetch_history(existing[0]["issue"], opener=opener)
    merged = merge_draws(existing, incoming)
    added = len(merged) - len(existing)
    if added > 0:
        write_json_atomic(data_path, merged)
    return {
        "added": added,
        "total": len(merged),
        "latest_issue": merged[0]["issue"],
        "latest_date": merged[0]["date"],
        "draws": merged,
    }

