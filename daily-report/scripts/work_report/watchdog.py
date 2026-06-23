"""Lightweight watchdog, ported from the monolith's --watchdog mode.

Meant to run on every launchd wake (the guard calls it each time). Two checks,
both internally throttled so it stays quiet unless the user needs to act:

  - token health:   alert when the Feishu refresh token expires within 3 days.
  - report freshness: alert when a past workday's report is overdue and still
                      missing (no done-flag), e.g. the laptop was asleep/offline.

State (last-alert timestamps) lives in flag_dir/watchdog_alert.state so alerts
don't spam. Done-flags (flag_dir/daily_YYYY-MM-DD.done) are written by the guard.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable

from work_report.lark_client import LarkClient
from work_report.models import WEEKDAY_CN


def _flag_dir(config: dict[str, Any]) -> Path:
    return Path(str(config.get("flag_dir") or "~/.report_flags")).expanduser()


def _due_by(d: date) -> datetime:
    """Deadline by which workday d's report must exist: next workday at 14:00
    (Friday's is due Monday — weekend closure is normal, the guard backfills)."""
    nxt = d + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return datetime.combine(nxt, time(hour=14))


def _recently_alerted(state_path: Path, key: str, hours: float) -> bool:
    try:
        last = json.loads(state_path.read_text()).get(key)
    except Exception:
        return False
    if not last:
        return False
    try:
        return (datetime.now() - datetime.fromisoformat(last)).total_seconds() < hours * 3600
    except ValueError:
        return False


def _mark_alerted(state_path: Path, key: str) -> None:
    try:
        data = json.loads(state_path.read_text())
    except Exception:
        data = {}
    data[key] = datetime.now().isoformat()
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(data))
    except OSError:
        pass


def _send(lark: Any, notify_user_id: str, notify_as: str, markdown: str) -> None:
    if not notify_user_id:
        return
    args = ["im", "+messages-send", "--user-id", notify_user_id, "--markdown", markdown]
    if notify_as:
        args.extend(["--as", notify_as])
    lark.call(args, timeout=30)


def check_token_health(
    lark: Any,
    notify_user_id: str,
    notify_as: str,
    state_path: Path,
    log: Callable[[str], None] = print,
) -> None:
    status = lark.call(["auth", "status"], timeout=15)
    exp = status.get("refreshExpiresAt") or status.get("data", {}).get("refreshExpiresAt")
    if not exp:
        return
    try:
        refresh_exp = datetime.fromisoformat(str(exp))
    except ValueError:
        return
    days_left = (refresh_exp - datetime.now(refresh_exp.tzinfo)).days
    if days_left <= 3 and not _recently_alerted(state_path, "token", hours=24):
        _send(
            lark,
            notify_user_id,
            notify_as,
            "⚠️ **飞书授权即将到期**\n\n"
            f"Token 将在 **{days_left} 天后**失效。\n"
            "请运行：`lark-cli auth login --domain im,calendar,drive,docs,contact,minutes --no-wait --json`",
        )
        _mark_alerted(state_path, "token")
        log(f"  ⚠️ Token 剩余 {days_left} 天，已发预警")


def check_report_freshness(
    lark: Any,
    notify_user_id: str,
    notify_as: str,
    flag_dir: Path,
    state_path: Path,
    log: Callable[[str], None] = print,
    today: date | None = None,
) -> None:
    now = datetime.now()
    today = today or now.date()
    missed: date | None = None
    for back in range(0, 9):
        d = today - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        if now < _due_by(d):
            continue
        if not (flag_dir / f"daily_{d}.done").exists():
            missed = d
            break
    if missed is None:
        return
    key = f"missed_{missed}"
    if _recently_alerted(state_path, key, hours=12):
        return
    days_ago = (today - missed).days
    weekday = WEEKDAY_CN[missed.weekday()]
    _send(
        lark,
        notify_user_id,
        notify_as,
        "⚠️ **日报漏推提醒**\n\n"
        f"{missed.strftime('%Y年%m月%d日')}（周{weekday}）的日报至今未生成"
        f"（已逾期约 {days_ago} 天）。\n"
        "多半是当晚电脑在睡眠/未联网，定时任务没被唤起。\n\n"
        "• 打开电脑联网后通常会自动补；\n"
        "• 若仍未恢复，手动重跑当天日报即可。",
    )
    _mark_alerted(state_path, key)
    log(f"  ⚠️ 已发漏推告警: {missed}（逾期 {days_ago} 天）")


def run_watchdog(
    config: dict[str, Any],
    binaries: dict[str, str],
    log: Callable[[str], None] = print,
    today: date | None = None,
) -> int:
    lark_cli = binaries.get("lark") or ""
    if not lark_cli:
        log("lark-cli: missing; watchdog skipped")
        return 0
    lark = LarkClient(lark_cli)
    notify_user_id = str(config.get("notify_user_id") or "")
    notify_as = str(config.get("notify_as") or "user")
    flag_dir = _flag_dir(config)
    state_path = flag_dir / "watchdog_alert.state"
    check_token_health(lark, notify_user_id, notify_as, state_path, log)
    check_report_freshness(lark, notify_user_id, notify_as, flag_dir, state_path, log, today)
    return 0
