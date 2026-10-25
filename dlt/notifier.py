"""将大乐透候选号码发送到可配置的消息通知渠道。"""

from __future__ import annotations

import json
import re
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PUSHPLUS_ENDPOINT = "https://www.pushplus.plus/send"
QMSG_ENDPOINT = "https://qmsg.zendee.cn/v3/jsend/{key}"
MAX_RESPONSE_BYTES = 65_536
QMSG_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


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


def format_prediction_text(prediction: dict[str, Any]) -> str:
    """生成适合 QQ 单聊的纯文本消息，避免 Markdown 标记影响可读性。"""
    target_issue = str(prediction["target_issue"])
    based_on_issue = str(prediction.get("based_on_issue", "-"))
    if len(target_issue) == 5 and target_issue.isdigit():
        target_issue = f"{target_issue[:2]}-{target_issue[2:]}"
    if len(based_on_issue) == 5 and based_on_issue.isdigit():
        based_on_issue = f"{based_on_issue[:2]}-{based_on_issue[2:]}"
    lines = [
        f"大乐透 {target_issue} 期候选",
        f"预计开奖日期：{prediction.get('target_date', '待定')}",
        f"数据截至：{based_on_issue} 期",
        "",
    ]
    for index, item in enumerate(prediction.get("recommendations", []), start=1):
        lines.extend(
            [
                f"{index}. {item.get('label', item.get('strategy', '候选'))}策略（评分 {item.get('score', '-')}）",
                f"前区：{' '.join(item['front'])}",
                f"后区：{' '.join(item['back'])}",
                "",
            ]
        )
    lines.extend(
        [
            "开奖结果具有独立随机性；模型评分只用于候选排序，不等同于中奖概率。",
            "请设置预算上限，理性购彩。",
        ]
    )
    return "\n".join(lines)


class QmsgNotifier:
    """通过 Qmsg酱把纯文本候选发送到绑定的 QQ 单聊或群。"""

    def __init__(
        self,
        key: str,
        *,
        group: str | None = None,
        opener: Callable[..., Any] = urlopen,
        timeout: int = 15,
    ) -> None:
        key = key.strip()
        if not QMSG_KEY_RE.fullmatch(key):
            raise NotificationConfigError("QMSG_KEY 未配置或格式异常")
        group = group.strip() if group else None
        if group is not None and (not group.isdigit() or len(group) > 20):
            raise NotificationConfigError("QMSG_GROUP 必须是 QQ 群号")
        self._endpoint = QMSG_ENDPOINT.format(key=key)
        self.group = group
        self.notification_id = f"qmsg:{group or 'direct'}"
        self._opener = opener
        self.timeout = timeout

    def send(self, prediction: dict[str, Any]) -> dict[str, Any]:
        message = format_prediction_text(prediction)
        if len(message) > 1800:
            raise NotificationError("Qmsg 消息超过 1800 字符限制")
        payload: dict[str, Any] = {"msg": message}
        if self.group:
            payload["group"] = self.group
        request = Request(
            self._endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": "DLTDataBot/0.3"},
            method="POST",
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:
                response_body = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            # API Key 位于 URL 路径中，只读取安全的状态码和响应消息，不能输出完整 URL。
            try:
                error_body = exc.read(MAX_RESPONSE_BYTES).decode("utf-8", errors="replace")
                error_value = json.loads(error_body)
                detail = error_value.get("message") or error_value.get("reason") or "request rejected"
            except (AttributeError, json.JSONDecodeError):
                detail = "request rejected"
            raise NotificationError(f"Qmsg HTTP {exc.code}: {detail}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise NotificationError("Qmsg 网络请求失败") from exc
        if len(response_body) > MAX_RESPONSE_BYTES:
            raise NotificationError("Qmsg 响应超过大小限制")
        try:
            result = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NotificationError("Qmsg 返回了无效 JSON") from exc
        if result.get("success") is not True:
            raise NotificationError(f"Qmsg 未接受消息: {result.get('message', 'unknown error')}")
        return result


class PushPlusNotifier:
    def __init__(
        self,
        token: str,
        *,
        channel: str = "wechat",
        topic: str | None = None,
        recipient: str | None = None,
        option: str | None = None,
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
        self.notification_id = f"pushplus:{channel}"
        self.topic = topic.strip() if topic else None
        self.recipient = recipient.strip() if recipient else None
        self.option = option.strip() if option else None
        if self.channel == "qq" and self.option and (self.topic or self.recipient):
            raise NotificationConfigError("QQ 群/指定机器人配置不能与 PUSHPLUS_TOPIC 或 PUSHPLUS_TO 同时使用")
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
        if self.option:
            payload["option"] = self.option
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

