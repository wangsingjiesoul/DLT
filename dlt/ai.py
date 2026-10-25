"""OpenAI-compatible AI 候选重排客户端。"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


MAX_AI_RESPONSE_BYTES = 262_144


class AIConfigError(ValueError):
    """AI 配置缺失或存在不安全值。"""


class AISelectionError(RuntimeError):
    """AI 服务调用失败或返回内容不可用。"""


def _chat_completions_endpoint(base_url: str) -> str:
    """校验基础地址并补齐 OpenAI-compatible 的标准接口路径。"""
    value = base_url.strip().rstrip("/")
    if not value or len(value) > 2_048:
        raise AIConfigError("AI_BASE_URL 未配置或长度异常")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise AIConfigError("AI_BASE_URL 必须是有效的 http 或 https 地址")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise AIConfigError("AI_BASE_URL 不能包含账号、密码、查询参数或锚点")
    path = parsed.path.rstrip("/")
    if not path.endswith("/chat/completions"):
        path = f"{path}/chat/completions"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _content_json(content: str) -> dict[str, Any]:
    """解析模型文本；兼容某些模型习惯添加的 Markdown 代码围栏。"""
    value = content.strip()
    if value.startswith("```") and value.endswith("```"):
        lines = value.splitlines()
        if len(lines) >= 3:
            value = "\n".join(lines[1:-1]).strip()
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AISelectionError("AI 模型未返回有效 JSON") from exc
    if not isinstance(parsed, dict):
        raise AISelectionError("AI 模型返回的 JSON 顶层必须是对象")
    return parsed


class OpenAICompatibleSelector:
    """通过 chat/completions 接口让外部模型选择候选 ID。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: int = 30,
        opener: Callable[..., Any] = urlopen,
        max_response_bytes: int = MAX_AI_RESPONSE_BYTES,
    ) -> None:
        """校验运行时配置并保存连接参数；API Key 永远只保存在实例内存中。"""
        key = api_key.strip()
        model = model.strip()
        if not key or len(key) > 4_096 or "\r" in key or "\n" in key:
            raise AIConfigError("AI_API_KEY 未配置或格式异常")
        if not model or len(model) > 200 or "\r" in model or "\n" in model:
            raise AIConfigError("AI_MODEL 未配置或格式异常")
        if not 1 <= timeout <= 120:
            raise AIConfigError("AI_TIMEOUT 必须在 1 到 120 秒之间")
        if not 1_024 <= max_response_bytes <= 1_048_576:
            raise AIConfigError("AI 响应大小限制无效")

        self._endpoint = _chat_completions_endpoint(base_url)
        self._api_key = key
        self.model = model
        self.timeout = timeout
        self._opener = opener
        self.max_response_bytes = max_response_bytes
        fingerprint = hashlib.sha256(f"{self._endpoint}|{self.model}".encode("utf-8")).hexdigest()[:12]
        self.profile_id = f"openai-compatible:{fingerprint}"

    def select(self, selection_request: dict[str, Any]) -> dict[str, Any]:
        """发送候选摘要并返回模型选择；网络与响应异常会被转换为安全错误。"""
        system_prompt = (
            "你是大乐透候选组合重排器。你不能创造、修改或补充号码，只能从用户提供的候选 ID 中选择。"
            "候选中的频率、遗漏和结构字段已经由历史开奖计算。"
            "必须为 balanced、trend、explore 各选择且只选择一个候选，兼顾统计分、历史结构常见度和三组覆盖差异。"
            "不要把热号、冷号、遗漏或 AI 判断描述为概率优势。"
            "只返回 JSON 对象，格式为："
            '{"selections":[{"strategy":"balanced","candidate_id":"...","reason":"不超过80字"},'
            '{"strategy":"trend","candidate_id":"...","reason":"不超过80字"},'
            '{"strategy":"explore","candidate_id":"...","reason":"不超过80字"}]}。'
            "开奖结果是独立随机事件，不得声称选择能提高中奖概率。"
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(selection_request, ensure_ascii=False, separators=(",", ":")),
                },
            ],
        }
        request = Request(
            self._endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
                "User-Agent": "DLTDataBot/0.4",
            },
            method="POST",
        )
        try:
            # 只读取有限大小的响应，防止异常服务返回超大内容占满内存。
            with self._opener(request, timeout=self.timeout) as response:
                response_body = response.read(self.max_response_bytes + 1)
        except HTTPError as exc:
            raise AISelectionError(f"AI 服务返回 HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise AISelectionError("AI 服务网络请求失败") from exc
        if len(response_body) > self.max_response_bytes:
            raise AISelectionError("AI 服务响应超过大小限制")
        try:
            result = json.loads(response_body.decode("utf-8"))
            content = result["choices"][0]["message"]["content"]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise AISelectionError("AI 服务返回了无法识别的响应") from exc
        if not isinstance(content, str):
            raise AISelectionError("AI 模型响应内容不是文本 JSON")
        return _content_json(content)
