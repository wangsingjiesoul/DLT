"""将大乐透候选号码发送到 PushPlus 微信或 QQ 渠道。"""

from __future__ import annotations

import json
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PUSHPLUS_ENDPOINT = "https://www.pushplus.plus/send"
MAX_RESPONSE_BYTES = 65_536


class NotificationConfigError(ValueError):
    """通知配置不完整或不安全。"""


class NotificationError(RuntimeError):
    """通知服务拒绝或未完成请求。"""


def format_prediction_markdown(prediction: dict[str, Any]) -> str:
    lines = [
        f"## 大乐透 {prediction['target_issue']} 期候选",
        "",
        f"预计开奖日期：**{prediction.get('target_date', '待定')}**",
        f"数据截至：{prediction.get('based_on_issue', '-')} 期",
        "",
    ]
    for index, item in enumerate(prediction.get("recommendations", []), start=1):
        front = " ".join(item["front"])
        back = " ".join(item["back"])
        lines.extend(
            [
                f"### {index}. {item.get('label', item.get('strategy', '候选'))}策略 · 评分 {item.get('score', '-')}",
                f"- 前区：**{front}**",
                f"- 后区：**{back}**",
                "",
            ]
        )
    lines.extend(
        [
            "> 开奖结果具有独立随机性；模型评分只用于候选排序，不等同于中奖概率。",
            "> 请设置预算上限，理性购彩。",
        ]
    )
    return "\n".join(lines)


class PushPlusNotifier:
    def __init__(
        self,
        token: str,
        *,
        channel: str = "wechat",
        topic: str | None = None,
        recipient: str | None = None,
        opener: Callable[..., Any] = urlopen,
        timeout: int = 15,
    ) -> None:
        token = token.strip()
        channel = channel.strip().lower()
        if not token or len(token) > 256:
            raise NotificationConfigError("PUSHPLUS_TOKEN 未配置或长度异常")
        if channel not in {"wechat", "qq"}:
            raise NotificationConfigError("PUSHPLUS_CHANNEL 仅支持 wechat 或 qq")
        self._token = token
        self.channel = channel
        self.topic = topic.strip() if topic else None
        self.recipient = recipient.strip() if recipient else None
        self._opener = opener
        self.timeout = timeout

    def send(self, prediction: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "token": self._token,
            "title": f"大乐透 {prediction['target_issue']} 期候选号码",
            "content": format_prediction_markdown(prediction),
            "template": "markdown",
            "channel": self.channel,
        }
        if self.topic:
            payload["topic"] = self.topic
        if self.recipient:
            payload["to"] = self.recipient
        request = Request(
            PUSHPLUS_ENDPOINT,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": "DLTDataBot/0.2"},
            method="POST",
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:
                response_body = response.read(MAX_RESPONSE_BYTES + 1)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise NotificationError(f"PushPlus 请求失败: {exc}") from exc
        if len(response_body) > MAX_RESPONSE_BYTES:
            raise NotificationError("PushPlus 响应超过大小限制")
        try:
            result = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NotificationError("PushPlus 返回了无效 JSON") from exc
        if result.get("code") != 200:
            raise NotificationError(f"PushPlus 未接受消息: {result.get('msg', 'unknown error')}")
        return result

