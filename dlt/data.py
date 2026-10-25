"""大乐透 JSON 数据的加载、校验与安全写入。"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import date
from pathlib import Path
from typing import Any


# 统一把期号保存为 5 位字符串、号码保存为 2 位字符串，避免数字 01 被变成 1。
ISSUE_RE = re.compile(r"^\d{5}$")
NUMBER_RE = re.compile(r"^\d{2}$")


class DataValidationError(ValueError):
    """开奖数据不符合预期结构。"""


def _validate_numbers(values: Any, *, count: int, minimum: int, maximum: int, field: str) -> list[str]:
    """校验前区或后区号码的数量、格式、范围、去重和顺序。"""
    if not isinstance(values, list) or len(values) != count:
        raise DataValidationError(f"{field} 必须包含 {count} 个号码")
    if not all(isinstance(item, str) and NUMBER_RE.fullmatch(item) for item in values):
        raise DataValidationError(f"{field} 号码必须是两位数字字符串")
    if len(set(values)) != count:
        raise DataValidationError(f"{field} 号码不能重复")
    if not all(minimum <= int(item) <= maximum for item in values):
        raise DataValidationError(f"{field} 号码超出 {minimum:02d}-{maximum:02d}")
    if values != sorted(values):
        raise DataValidationError(f"{field} 号码必须按升序排列")
    return list(values)


def validate_draw(draw: Any) -> dict[str, Any]:
    """校验并返回只包含规范字段的开奖记录。"""
    if not isinstance(draw, dict):
        raise DataValidationError("开奖记录必须是对象")

    issue = draw.get("issue")
    draw_date = draw.get("date")
    if not isinstance(issue, str) or not ISSUE_RE.fullmatch(issue):
        raise DataValidationError("issue 必须是 5 位期号")
    if not isinstance(draw_date, str):
        raise DataValidationError("date 必须是 YYYY-MM-DD 字符串")
    try:
        date.fromisoformat(draw_date)
    except ValueError as exc:
        raise DataValidationError("date 不是有效日期") from exc

    return {
        "issue": issue,
        "date": draw_date,
        "front": _validate_numbers(draw.get("front"), count=5, minimum=1, maximum=35, field="front"),
        "back": _validate_numbers(draw.get("back"), count=2, minimum=1, maximum=12, field="back"),
    }


def validate_draws(draws: Any) -> list[dict[str, Any]]:
    """校验整份历史数据，去除多余字段，并统一按最新日期在前排序。"""
    if not isinstance(draws, list) or not draws:
        raise DataValidationError("开奖数据必须是非空数组")
    normalized = [validate_draw(draw) for draw in draws]
    issues = [draw["issue"] for draw in normalized]
    dates = [draw["date"] for draw in normalized]
    if len(set(issues)) != len(issues):
        raise DataValidationError("存在重复期号")
    if len(set(dates)) != len(dates):
        raise DataValidationError("存在重复开奖日期")
    normalized.sort(key=lambda item: (item["date"], item["issue"]), reverse=True)
    return normalized


def load_draws(path: str | Path) -> list[dict[str, Any]]:
    """从 UTF-8 JSON 文件读取数据；读取后仍要校验，不能盲目信任磁盘内容。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_draws(data)


def write_json_atomic(path: str | Path, value: Any) -> None:
    """在目标目录内先写临时文件，再原子替换，避免中断时破坏 JSON。"""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
        delete=False,
    )
    temp_path = Path(handle.name)
    try:
        with handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, destination)
    finally:
        if temp_path.exists():
            temp_path.unlink()

