"""大乐透推荐服务命令行入口。"""

from __future__ import annotations

import argparse
import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from time import sleep
from zoneinfo import ZoneInfo

from dlt.automation import AutomationRunner
from dlt.backtest import run_backtest
from dlt.data import load_draws, write_json_atomic
from dlt.notifier import (
    NotificationConfigError,
    PushPlusNotifier,
    QmsgNotifier,
    format_prediction_markdown,
    format_prediction_text,
)
from dlt.predictor import generate_prediction
from dlt.updater import update_data
from dlt.web import serve


ROOT = Path(__file__).resolve().parent
DATA_PATH = ROOT / "data" / "dlt.json"
RUNTIME_DIR = ROOT / "runtime"
WEB_ROOT = ROOT / "web"
LOG = logging.getLogger("dlt")


def load_env_file(path: Path = ROOT / ".env") -> None:
    """读取简单 KEY=VALUE 配置；已有系统环境变量优先。"""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key.replace("_", "").isalnum():
            os.environ.setdefault(key, value)


def notification_provider() -> str:
    provider = os.getenv("NOTIFY_PROVIDER", "").strip().lower()
    if not provider:
        if os.getenv("QMSG_KEY", "").strip():
            provider = "qmsg"
        elif os.getenv("PUSHPLUS_TOKEN", "").strip():
            provider = "pushplus"
        else:
            provider = "qmsg"
    if provider not in {"qmsg", "pushplus"}:
        raise NotificationConfigError("NOTIFY_PROVIDER 仅支持 qmsg 或 pushplus")
    return provider


def build_notifiers(required: bool = False) -> list[QmsgNotifier | PushPlusNotifier]:
    provider = notification_provider()

    if provider == "qmsg":
        key = os.getenv("QMSG_KEY", "").strip()
        if not key:
            if required:
                raise NotificationConfigError("请先配置 QMSG_KEY")
            return []
        return [QmsgNotifier(key, group=os.getenv("QMSG_GROUP") or None)]

    if provider == "pushplus":
        token = os.getenv("PUSHPLUS_TOKEN", "").strip()
        if not token:
            if required:
                raise NotificationConfigError("请先配置 PUSHPLUS_TOKEN")
            return []
        raw_channels = os.getenv("PUSHPLUS_CHANNELS", "").strip()
        channels = [value.strip().lower() for value in raw_channels.split(",") if value.strip()]
        if not channels:
            channels = [os.getenv("PUSHPLUS_CHANNEL", "wechat").strip().lower()]
        channels = list(dict.fromkeys(channels))
        return [
            PushPlusNotifier(
                token,
                channel=channel,
                topic=os.getenv("PUSHPLUS_TOPIC") or None,
                recipient=os.getenv("PUSHPLUS_TO") or None,
                option=(os.getenv("PUSHPLUS_OPTION") or None) if channel == "qq" else None,
            )
            for channel in channels
        ]

    raise AssertionError("unreachable")


def build_runner(*, notifier_required: bool = False) -> AutomationRunner:
    return AutomationRunner(
        data_path=DATA_PATH,
        runtime_dir=RUNTIME_DIR,
        notifiers=build_notifiers(required=notifier_required),
    )


def _schedule_config() -> tuple[int, int, set[int]]:
    raw_time = os.getenv("DLT_SCHEDULE_TIME", "22:35")
    try:
        hour_text, minute_text = raw_time.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
        weekdays = {int(value.strip()) for value in os.getenv("DLT_SCHEDULE_WEEKDAYS", "0,2,5").split(",")}
        if not weekdays or not weekdays <= set(range(7)):
            raise ValueError
    except ValueError as exc:
        raise ValueError("定时配置无效：DLT_SCHEDULE_TIME 应为 HH:MM，星期应为 0-6") from exc
    return hour, minute, weekdays


def start_scheduler(runner: AutomationRunner) -> threading.Thread:
    hour, minute, weekdays = _schedule_config()
    timezone = ZoneInfo(os.getenv("DLT_TIMEZONE", "Asia/Shanghai"))
    retry_minutes = max(1, int(os.getenv("DLT_SCHEDULE_RETRY_MINUTES", "10")))

    def loop() -> None:
        last_success_date = None
        last_attempt_at = None
        LOG.info(
            "自动任务已启用：星期 %s %02d:%02d %s，未更新时每 %s 分钟重试",
            sorted(weekdays), hour, minute, timezone.key, retry_minutes,
        )
        while True:
            now = datetime.now(timezone)
            after_start = (now.hour, now.minute) >= (hour, minute)
            retry_due = last_attempt_at is None or (now - last_attempt_at).total_seconds() >= retry_minutes * 60
            due = now.weekday() in weekdays and after_start and last_success_date != now.date() and retry_due
            if due:
                last_attempt_at = now
                try:
                    result = runner.run(update=True, notify=True, expected_latest_date=now.date().isoformat())
                    if result["fresh"]:
                        last_success_date = now.date()
                        LOG.info(
                            "自动周期完成：目标期号=%s，已通知=%s",
                            result["prediction"]["target_issue"], result["notified"],
                        )
                    else:
                        LOG.warning("开奖源尚未更新到 %s，将稍后重试", now.date().isoformat())
                except Exception:
                    LOG.exception("自动周期执行失败")
            sleep(20)

    thread = threading.Thread(target=loop, name="dlt-scheduler", daemon=True)
    thread.start()
    return thread


def command_update(_args: argparse.Namespace) -> int:
    result = update_data(DATA_PATH)
    print(json.dumps({key: value for key, value in result.items() if key != "draws"}, ensure_ascii=False, indent=2))
    return 0


def command_predict(_args: argparse.Namespace) -> int:
    prediction = generate_prediction(load_draws(DATA_PATH))
    write_json_atomic(RUNTIME_DIR / "latest_prediction.json", prediction)
    print(json.dumps(prediction, ensure_ascii=False, indent=2))
    return 0


def command_notify(args: argparse.Namespace) -> int:
    prediction = generate_prediction(load_draws(DATA_PATH))
    if args.dry_run:
        formatter = format_prediction_text if notification_provider() == "qmsg" else format_prediction_markdown
        print(formatter(prediction))
        return 0
    results = {notifier.notification_id: notifier.send(prediction) for notifier in build_notifiers(required=True)}
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


def command_run_once(args: argparse.Namespace) -> int:
    runner = build_runner(notifier_required=args.notify)
    result = runner.run(update=not args.no_update, notify=args.notify, force_notify=args.force_notify)
    output = {
        "target_issue": result["prediction"]["target_issue"],
        "based_on_issue": result["prediction"]["based_on_issue"],
        "notified": result["notified"],
        "notified_channels": result["notified_channels"],
        "update": {key: value for key, value in (result["update"] or {}).items() if key != "draws"},
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def command_backtest(args: argparse.Namespace) -> int:
    result = run_backtest(load_draws(DATA_PATH), rounds=args.rounds, candidate_count=args.candidates)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def command_serve(args: argparse.Namespace) -> int:
    # 启用调度器就必须配置通知渠道，避免服务看似正常运行、实际无人收到消息。
    runner = build_runner(notifier_required=not args.no_scheduler)
    if not args.no_scheduler:
        start_scheduler(runner)
    serve(host=args.host, port=args.port, data_path=DATA_PATH, runtime_dir=RUNTIME_DIR, web_root=WEB_ROOT)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="大乐透历史数据、候选推荐和通知服务")
    subparsers = parser.add_subparsers(dest="command", required=True)

    update_parser = subparsers.add_parser("update", help="增量更新历史开奖数据")
    update_parser.set_defaults(handler=command_update)

    predict_parser = subparsers.add_parser("predict", help="生成并保存下一期候选")
    predict_parser.set_defaults(handler=command_predict)

    notify_parser = subparsers.add_parser("notify", help="通过已配置渠道发送当前候选")
    notify_parser.add_argument("--dry-run", action="store_true", help="只打印消息，不发送")
    notify_parser.set_defaults(handler=command_notify)

    run_parser = subparsers.add_parser("run-once", help="手动执行一次自动化周期")
    run_parser.add_argument("--no-update", action="store_true", help="跳过开奖数据更新")
    run_parser.add_argument("--notify", action="store_true", help="发送通知")
    run_parser.add_argument("--force-notify", action="store_true", help="即使本期已发送也再次通知")
    run_parser.set_defaults(handler=command_run_once)

    backtest_parser = subparsers.add_parser("backtest", help="按时间顺序比较模型和随机基线")
    backtest_parser.add_argument("--rounds", type=int, default=30, help="回测最近多少期")
    backtest_parser.add_argument("--candidates", type=int, default=250, help="每种策略生成的候选数")
    backtest_parser.set_defaults(handler=command_backtest)

    serve_parser = subparsers.add_parser("serve", help="启动网页、API 和定时任务")
    serve_parser.add_argument("--host", default=os.getenv("DLT_HOST", "127.0.0.1"))
    serve_parser.add_argument("--port", type=int, default=int(os.getenv("DLT_PORT", "8000")))
    serve_parser.add_argument("--no-scheduler", action="store_true", help="只启动 Web，不运行定时任务")
    serve_parser.set_defaults(handler=command_serve)
    return parser


def main() -> int:
    load_env_file()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args()
    try:
        return args.handler(args)
    except (ValueError, OSError, RuntimeError) as exc:
        LOG.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
