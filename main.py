"""大乐透推荐服务命令行入口。"""

from __future__ import annotations

import argparse
import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from time import sleep
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dlt.ai import AIConfigError, AISelectionError, OpenAICompatibleSelector
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
from dlt.predictor import generate_prediction, prediction_profile
from dlt.updater import update_data
from dlt.web import serve


ROOT = Path(__file__).resolve().parent
DATA_PATH = ROOT / "data" / "dlt.json"
RUNTIME_DIR = ROOT / "runtime"
WEB_ROOT = ROOT / "web"
LOG = logging.getLogger("dlt")

# 这个类型别名表示“输入开奖列表，输出预测字典”的函数，便于初学者理解依赖注入。
PredictFunction = Callable[[list[dict[str, Any]]], dict[str, Any]]


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


def _env_flag(name: str, default: bool = False) -> bool:
    """把环境变量中的 true/false 文本转换成 Python 布尔值。"""
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default
    value = raw_value.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise AIConfigError(f"{name} 必须填写 true 或 false")


def build_ai_selector() -> tuple[OpenAICompatibleSelector | None, bool]:
    """从环境变量构造 AI 客户端；密钥只在内存中使用。"""
    enabled = _env_flag("AI_ENABLED")
    required = _env_flag("AI_REQUIRED")
    if required and not enabled:
        raise AIConfigError("AI_REQUIRED=true 时必须同时设置 AI_ENABLED=true")
    if not enabled:
        return None, False
    try:
        timeout = int(os.getenv("AI_TIMEOUT", "30"))
    except ValueError as exc:
        raise AIConfigError("AI_TIMEOUT 必须是整数秒") from exc
    selector = OpenAICompatibleSelector(
        base_url=os.getenv("AI_BASE_URL", "https://api.openai.com/v1"),
        api_key=os.getenv("AI_API_KEY", ""),
        model=os.getenv("AI_MODEL", ""),
        timeout=timeout,
    )
    return selector, required


def build_predict_function() -> tuple[PredictFunction, str]:
    """把 AI 配置封装进预测函数，供 CLI、定时器和 Web 共同复用。"""
    selector, required = build_ai_selector()

    def predict(draws: list[dict[str, Any]]) -> dict[str, Any]:
        return generate_prediction(draws, ai_selector=selector, ai_required=required)

    return predict, prediction_profile(selector, required)


def notification_provider() -> str:
    """确定使用哪个通知服务，并兼容旧版“根据已配置密钥自动判断”。"""
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
    """根据环境变量构造一个或多个通知器。"""
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


def build_runner(*, notifier_required: bool = False, predict_func: PredictFunction | None = None) -> AutomationRunner:
    """组装自动化流程；Web、手动执行和定时任务都复用它。"""
    if predict_func is None:
        predict_func, _profile = build_predict_function()
    return AutomationRunner(
        data_path=DATA_PATH,
        runtime_dir=RUNTIME_DIR,
        notifiers=build_notifiers(required=notifier_required),
        predict_func=predict_func,
    )


def _schedule_config() -> tuple[int, int, set[int]]:
    """解析每天执行时刻和开奖星期，发现错误时尽早停止启动。"""
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


def _retry_minutes() -> int:
    """读取开奖未更新时的重试间隔，并拒绝非整数配置。"""
    try:
        return max(1, int(os.getenv("DLT_SCHEDULE_RETRY_MINUTES", "10")))
    except ValueError as exc:
        raise ValueError("DLT_SCHEDULE_RETRY_MINUTES 必须是整数分钟") from exc


def _load_timezone(name: str) -> tuple[tzinfo, str]:
    """读取 IANA 时区；Windows 缺少时区库时为北京时间提供安全后备。

    Linux/Docker 会优先使用完整的 ``Asia/Shanghai`` 规则。部分 Windows
    Python 没有自带 IANA 数据，但中国大陆自 1991 年后一直使用 UTC+8，
    因此本项目面向当前开奖调度时可安全退回固定 UTC+8。
    """
    try:
        return ZoneInfo(name), "iana"
    except ZoneInfoNotFoundError as exc:
        if name == "Asia/Shanghai":
            return timezone(timedelta(hours=8), name), "fixed-utc+8-fallback"
        raise ValueError(f"找不到时区：{name}") from exc


def _safe_notifier_id(notifier: QmsgNotifier | PushPlusNotifier) -> str:
    """返回不包含群号、接收人和凭证的通知渠道名称。"""
    if isinstance(notifier, QmsgNotifier):
        return "qmsg:group" if notifier.group else "qmsg:direct"
    return f"pushplus:{notifier.channel}"


def build_doctor_report(
    *,
    data_path: Path = DATA_PATH,
    runtime_dir: Path = RUNTIME_DIR,
) -> dict[str, Any]:
    """执行不联网、不发消息的部署体检，并返回不含凭证的结构化结果。"""
    report: dict[str, Any] = {}
    core_ok = True

    try:
        draws = load_draws(data_path)
        report["data"] = {
            "ok": True,
            "draw_count": len(draws),
            "latest_issue": draws[0]["issue"],
            "latest_date": draws[0]["date"],
        }
    except (ValueError, OSError, json.JSONDecodeError):
        core_ok = False
        report["data"] = {"ok": False, "error": "历史数据读取或校验失败"}

    try:
        runtime_dir.mkdir(parents=True, exist_ok=True)
        # 临时文件在关闭后自动删除，用于验证 Docker 命名卷或本地目录确实可写。
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=runtime_dir, prefix=".doctor.") as handle:
            handle.write("ok")
            handle.flush()
        report["runtime"] = {"writable": True}
    except OSError:
        core_ok = False
        report["runtime"] = {"writable": False, "error": "运行目录不可写"}

    ai_config_valid = True
    try:
        selector, ai_required = build_ai_selector()
        report["ai"] = {
            "configured": selector is not None,
            "required": ai_required,
            "model": selector.model if selector is not None else None,
        }
    except (ValueError, OSError):
        ai_config_valid = False
        core_ok = False
        report["ai"] = {"configured": False, "required": False, "model": None, "error": "AI 配置无效"}

    notifier_config_valid = True
    try:
        notifiers = build_notifiers(required=False)
        report["notifications"] = {
            "configured": bool(notifiers),
            "channels": [_safe_notifier_id(notifier) for notifier in notifiers],
        }
    except (ValueError, OSError):
        notifier_config_valid = False
        core_ok = False
        report["notifications"] = {"configured": False, "channels": [], "error": "通知配置无效"}

    try:
        hour, minute, weekdays = _schedule_config()
        timezone_name = os.getenv("DLT_TIMEZONE", "Asia/Shanghai")
        _timezone, timezone_source = _load_timezone(timezone_name)
        retry_minutes = _retry_minutes()
        report["schedule"] = {
            "valid": True,
            "timezone": timezone_name,
            "timezone_source": timezone_source,
            "time": f"{hour:02d}:{minute:02d}",
            "weekdays": sorted(weekdays),
            "retry_minutes": retry_minutes,
        }
    except (ValueError, OSError, ZoneInfoNotFoundError):
        core_ok = False
        report["schedule"] = {"valid": False, "error": "定时或时区配置无效"}

    ai = report["ai"]
    notifications = report["notifications"]
    schedule = report["schedule"]
    deployment_ready = bool(
        core_ok
        and ai_config_valid
        and ai.get("configured")
        and ai.get("required")
        and notifier_config_valid
        and notifications.get("configured")
        and schedule.get("valid")
    )
    report["ok"] = core_ok
    report["deployment_ready"] = deployment_ready
    report["notice"] = "本检查不访问开奖源或 AI，也不会发送通知；请另用 ai-check 验证真实模型调用。"
    return report


def start_scheduler(runner: AutomationRunner) -> threading.Thread:
    """在后台线程中按北京时间触发更新；数据未发布时按间隔重试。"""
    hour, minute, weekdays = _schedule_config()
    timezone, _timezone_source = _load_timezone(os.getenv("DLT_TIMEZONE", "Asia/Shanghai"))
    retry_minutes = _retry_minutes()

    def loop() -> None:
        # 这些变量只存在内存中；真正的通知去重状态由 AutomationRunner 落盘。
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
                            "自动周期完成：目标期号=%s，AI=%s，已通知=%s",
                            result["prediction"]["target_issue"],
                            result["prediction"].get("ai", {}).get("status", "unknown"),
                            result["notified"],
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
    """CLI：只更新历史开奖文件。"""
    result = update_data(DATA_PATH)
    print(json.dumps({key: value for key, value in result.items() if key != "draws"}, ensure_ascii=False, indent=2))
    return 0


def command_predict(_args: argparse.Namespace) -> int:
    """CLI：生成当前配置对应的预测并保存。"""
    predict_func, _profile = build_predict_function()
    prediction = predict_func(load_draws(DATA_PATH))
    write_json_atomic(RUNTIME_DIR / "latest_prediction.json", prediction)
    print(json.dumps(prediction, ensure_ascii=False, indent=2))
    return 0


def command_notify(args: argparse.Namespace) -> int:
    """CLI：生成预测并立即发送；dry-run 只展示消息文本。"""
    predict_func, _profile = build_predict_function()
    prediction = predict_func(load_draws(DATA_PATH))
    if args.dry_run:
        formatter = format_prediction_text if notification_provider() == "qmsg" else format_prediction_markdown
        print(formatter(prediction))
        return 0
    results = {notifier.notification_id: notifier.send(prediction) for notifier in build_notifiers(required=True)}
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


def command_run_once(args: argparse.Namespace) -> int:
    """CLI：手动执行与定时器相同的一次完整周期。"""
    runner = build_runner(notifier_required=args.notify)
    result = runner.run(update=not args.no_update, notify=args.notify, force_notify=args.force_notify)
    output = {
        "target_issue": result["prediction"]["target_issue"],
        "based_on_issue": result["prediction"]["based_on_issue"],
        "notified": result["notified"],
        "notified_channels": result["notified_channels"],
        "ai": result["prediction"].get("ai"),
        "update": {key: value for key, value in (result["update"] or {}).items() if key != "draws"},
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def command_backtest(args: argparse.Namespace) -> int:
    """CLI：运行不调用外部 AI 的统计层滚动回测。"""
    result = run_backtest(
        load_draws(DATA_PATH),
        rounds=args.rounds,
        candidate_count=args.candidates,
        baseline_repeats=args.baseline_repeats,
    )
    # 保存最近一次审计，方便部署者比较模型版本；runtime 目录不会提交到 Git。
    write_json_atomic(RUNTIME_DIR / "latest_backtest.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def command_ai_check(args: argparse.Namespace) -> int:
    """CLI：进行一次严格的真实 AI 调用，但不更新数据、不保存结果、不发送通知。"""
    selector, _configured_required = build_ai_selector()
    if selector is None:
        raise AIConfigError("AI 尚未启用：请先在 .env 中配置 AI_ENABLED=true 及模型参数")
    if not 100 <= args.candidates <= 5_000:
        raise ValueError("--candidates 必须在 100 到 5000 之间")

    # 连通性检查始终强制严格模式，绝不把统计回退误报成 AI 调用成功。
    prediction = generate_prediction(
        load_draws(DATA_PATH),
        candidate_count=args.candidates,
        ai_selector=selector,
        ai_required=True,
    )
    ai_status = prediction.get("ai")
    if not isinstance(ai_status, dict) or not ai_status.get("used") or ai_status.get("status") != "selected":
        raise AISelectionError("AI 连通性检查未通过")
    print(
        json.dumps(
            {
                "ok": True,
                "model": ai_status.get("model"),
                "target_issue": prediction.get("target_issue"),
                "used": ai_status.get("used"),
                "status": ai_status.get("status"),
                "recommendation_count": len(prediction.get("recommendations", [])),
                "notice": "AI 已完成真实候选重排；本命令没有发送任何通知。",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def command_doctor(args: argparse.Namespace) -> int:
    """CLI：在不联网、不发消息的前提下检查部署所需配置。"""
    report = build_doctor_report()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.deployment:
        return 0 if report["deployment_ready"] else 1
    return 0 if report["ok"] else 1


def command_serve(args: argparse.Namespace) -> int:
    """CLI：启动网页/API，并按参数决定是否同时启动定时器。"""
    # 启用调度器就必须配置通知渠道，避免服务看似正常运行、实际无人收到消息。
    predict_func, profile = build_predict_function()
    runner = build_runner(notifier_required=not args.no_scheduler, predict_func=predict_func)
    if not args.no_scheduler:
        start_scheduler(runner)
    serve(
        host=args.host,
        port=args.port,
        data_path=DATA_PATH,
        runtime_dir=RUNTIME_DIR,
        web_root=WEB_ROOT,
        predict_func=predict_func,
        prediction_profile=profile,
        service_status={
            "ai_enabled": _env_flag("AI_ENABLED"),
            "ai_required": _env_flag("AI_REQUIRED"),
            "scheduler_enabled": not args.no_scheduler,
        },
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    """声明所有子命令和参数，argparse 会自动生成 --help。"""
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
    backtest_parser.add_argument("--baseline-repeats", type=int, default=30, help="每期重复随机基线次数")
    backtest_parser.set_defaults(handler=command_backtest)

    ai_check_parser = subparsers.add_parser("ai-check", help="真实调用一次 AI，安全检查配置和候选重排")
    ai_check_parser.add_argument("--candidates", type=int, default=300, help="每种策略的本地候选抽样次数")
    ai_check_parser.set_defaults(handler=command_ai_check)

    doctor_parser = subparsers.add_parser("doctor", help="离线检查数据、AI、通知、时区和部署就绪状态")
    doctor_parser.add_argument("--deployment", action="store_true", help="要求 AI 严格模式和通知渠道均已配置")
    doctor_parser.set_defaults(handler=command_doctor)

    serve_parser = subparsers.add_parser("serve", help="启动网页、API 和定时任务")
    serve_parser.add_argument("--host", default=os.getenv("DLT_HOST", "127.0.0.1"))
    serve_parser.add_argument("--port", type=int, default=int(os.getenv("DLT_PORT", "8000")))
    serve_parser.add_argument("--no-scheduler", action="store_true", help="只启动 Web，不运行定时任务")
    serve_parser.set_defaults(handler=command_serve)
    return parser


def main() -> int:
    """程序入口：读取配置、初始化日志、解析命令并统一处理可预期错误。"""
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
