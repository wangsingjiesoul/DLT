"""一次完整自动化周期：更新、预测、保存、按期号去重通知。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .data import load_draws, write_json_atomic
from .predictor import generate_prediction
from .updater import update_data


class AutomationRunner:
    def __init__(
        self,
        *,
        data_path: str | Path,
        runtime_dir: str | Path,
        update_func: Callable[..., Any] = update_data,
        predict_func: Callable[[list[dict[str, Any]]], dict[str, Any]] = generate_prediction,
        notifier: Any | None = None,
        load_func: Callable[[str | Path], list[dict[str, Any]]] = load_draws,
    ) -> None:
        self.data_path = Path(data_path)
        self.runtime_dir = Path(runtime_dir)
        self.prediction_path = self.runtime_dir / "latest_prediction.json"
        self.state_path = self.runtime_dir / "automation_state.json"
        self.update_func = update_func
        self.predict_func = predict_func
        self.notifier = notifier
        self.load_func = load_func

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {}
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def run(self, *, update: bool = True, notify: bool = True, force_notify: bool = False) -> dict[str, Any]:
        update_result = None
        if update:
            update_result = self.update_func(self.data_path)
        draws = self.load_func(self.data_path)
        prediction = self.predict_func(draws)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.prediction_path, prediction)

        state = self._load_state()
        notified = False
        notification_result = None
        if notify and self.notifier is not None:
            already_sent = state.get("last_notified_issue") == prediction["target_issue"]
            if force_notify or not already_sent:
                notification_result = self.notifier.send(prediction)
                state["last_notified_issue"] = prediction["target_issue"]
                write_json_atomic(self.state_path, state)
                notified = True

        return {
            "prediction": prediction,
            "update": update_result,
            "notified": notified,
            "notification": notification_result,
        }

