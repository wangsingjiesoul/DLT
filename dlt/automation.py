"""一次完整自动化周期：更新、预测、保存、按期号去重通知。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .data import load_draws, write_json_atomic
from .predictor import generate_prediction
from .updater import update_data


class AutomationRunner:
    """把更新、预测、保存和通知串成一次可重试的业务流程。"""

    def __init__(
        self,
        *,
        data_path: str | Path,
        runtime_dir: str | Path,
        update_func: Callable[..., Any] = update_data,
        predict_func: Callable[[list[dict[str, Any]]], dict[str, Any]] = generate_prediction,
        notifier: Any | None = None,
        notifiers: list[Any] | None = None,
        load_func: Callable[[str | Path], list[dict[str, Any]]] = load_draws,
    ) -> None:
        """依赖都可替换，既方便测试，也允许主程序注入带 AI 的预测函数。"""
        if notifier is not None and notifiers is not None:
            raise ValueError("notifier 和 notifiers 不能同时配置")
        self.data_path = Path(data_path)
        self.runtime_dir = Path(runtime_dir)
        self.prediction_path = self.runtime_dir / "latest_prediction.json"
        self.state_path = self.runtime_dir / "automation_state.json"
        self.update_func = update_func
        self.predict_func = predict_func
        self.notifiers = list(notifiers) if notifiers is not None else ([notifier] if notifier is not None else [])
        self.load_func = load_func

    @staticmethod
    def _notifier_id(notifier: Any, index: int) -> str:
        """给通知渠道一个稳定名称，用于记录哪个渠道已经成功。"""
        value = getattr(notifier, "notification_id", None)
        return str(value) if value else f"{notifier.__class__.__name__}:{index}"

    @staticmethod
    def _save_delivery_state(state: dict[str, Any], target_issue: str, sent_ids: set[str]) -> None:
        """把某一期已成功的渠道写回状态字典。"""
        deliveries = state.get("notified_channels")
        if not isinstance(deliveries, dict):
            deliveries = {}
        deliveries[target_issue] = sorted(sent_ids)
        # 只保留最近 20 个目标期号，避免运行状态无限增长。
        state["notified_channels"] = dict(list(deliveries.items())[-20:])

    def _load_state(self) -> dict[str, Any]:
        """读取运行状态；文件损坏时返回空状态，让后续流程可以重新尝试。"""
        if not self.state_path.exists():
            return {}
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def run(
        self,
        *,
        update: bool = True,
        notify: bool = True,
        force_notify: bool = False,
        expected_latest_date: str | None = None,
    ) -> dict[str, Any]:
        """运行一个完整周期，任何预测异常都会在发送消息之前向上抛出。"""
        update_result = None
        if update:
            update_result = self.update_func(self.data_path)
        draws = self.load_func(self.data_path)
        prediction = self.predict_func(draws)
        # 只有预测（包括严格模式下的 AI）成功后，才覆盖最近结果文件。
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.prediction_path, prediction)
        fresh = expected_latest_date is None or prediction.get("based_on_date") == expected_latest_date

        state = self._load_state()
        notified = False
        notification_result: dict[str, Any] | None = None
        notified_channels: list[str] = []
        if notify and self.notifiers and fresh:
            target_issue = prediction["target_issue"]
            deliveries = state.get("notified_channels")
            raw_sent = deliveries.get(target_issue, []) if isinstance(deliveries, dict) else []
            sent_ids = {str(value) for value in raw_sent if isinstance(value, str)}

            # 兼容旧版单通道状态：默认第一通道已经成功，升级后只补发其他通道。
            if not sent_ids and state.get("last_notified_issue") == target_issue:
                sent_ids.add(self._notifier_id(self.notifiers[0], 0))

            results: dict[str, Any] = {}
            for index, notifier in enumerate(self.notifiers):
                notifier_id = self._notifier_id(notifier, index)
                if not force_notify and notifier_id in sent_ids:
                    continue
                results[notifier_id] = notifier.send(prediction)
                sent_ids.add(notifier_id)
                notified_channels.append(notifier_id)
                notified = True
                # 每个渠道成功后立即落盘；后续渠道失败时，重试不会重复发送成功渠道。
                self._save_delivery_state(state, target_issue, sent_ids)
                write_json_atomic(self.state_path, state)

            configured_ids = {self._notifier_id(item, index) for index, item in enumerate(self.notifiers)}
            if configured_ids <= sent_ids:
                state["last_notified_issue"] = target_issue
                self._save_delivery_state(state, target_issue, sent_ids)
                write_json_atomic(self.state_path, state)
            notification_result = results or None

        return {
            "prediction": prediction,
            "update": update_result,
            "notified": notified,
            "notification": notification_result,
            "notified_channels": notified_channels,
            "fresh": fresh,
        }

