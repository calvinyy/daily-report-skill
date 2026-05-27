#!/usr/bin/env python3
"""
Generate a Feishu/Lark daily work report.

The script collects local work activity, asks Codex first and Claude second to
summarize it, writes the report to a weekly Feishu document, and can notify the
current user. It uses only Python's standard library plus external CLIs.

Faithful-recording rule: every Claude Code session and every Codex session for
the day is enumerated individually in the report — not collapsed into a single
vague line. Recurring hourly Codex automations are the one exception: they are
folded by automation ID into one entry with a run count.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


APP_DIR = Path.home() / ".daily-report-skill"
DEFAULT_CONFIG_PATH = APP_DIR / "config.json"
LOG_PATH = APP_DIR / "report.log"
WEEKDAY_CN = ["一", "二", "三", "四", "五", "六", "日"]

DEFAULT_SKIP_DOMAINS = {
    "google.com",
    "www.google.com",
    "accounts.google.com",
    "accounts.feishu.cn",
    "localhost",
    "127.0.0.1",
}

DEFAULT_CONFIG: dict[str, Any] = {
    "folder_name": "周报记录",
    "notify_user_id": "",
    "notify_as": "user",
    "send_notification": True,
    "lark_cli": "",
    "codex_cli": "",
    "claude_cli": "",
    "ai_cli": "",
    "ai_enabled": True,
    "timezone_offset": "+08:00",
    "chrome_profile": "Default",
    "skip_domains": sorted(DEFAULT_SKIP_DOMAINS),
}


def log(message: str) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
    print(line, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_config(path: Path) -> dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                config.update(loaded)
        except Exception as exc:
            raise SystemExit(f"配置文件读取失败: {path} ({exc})")
    return config


def save_default_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        print(f"配置文件已存在: {path}")
        return
    path.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已创建配置文件: {path}")


def find_binary(configured: str, name: str, extra_paths: list[str] | None = None) -> str:
    candidates = []
    if configured:
        candidates.append(configured)
    found = shutil.which(name)
    if found:
        candidates.append(found)
    candidates.extend(extra_paths or [])
    for candidate in candidates:
        if not candidate:
            continue
        expanded = os.path.expanduser(candidate)
        if os.path.isabs(expanded) and os.path.exists(expanded):
            return expanded
        resolved = shutil.which(expanded)
        if resolved:
            return resolved
    return ""


def command_preview(command: list[str]) -> str:
    if not command:
        return ""
    return " ".join(Path(part).name if idx == 0 else part for idx, part in enumerate(command[:4]))


def run_json(command: list[str], timeout: int = 45) -> dict[str, Any]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except Exception as exc:
        return {"ok": False, "_error": str(exc), "_command": command}

    raw = result.stdout.strip()
    if not raw:
        return {
            "ok": False,
            "_error": result.stderr.strip() or f"command exited {result.returncode}",
            "_command": command,
        }

    try:
        data = json.loads(raw)
    except Exception:
        return {
            "ok": False,
            "_error": result.stderr.strip() or raw[:500],
            "_command": command,
            "_returncode": result.returncode,
        }
    if result.returncode != 0 and "_error" not in data:
        data["_error"] = result.stderr.strip() or f"command exited {result.returncode}"
    return data


class LarkClient:
    def __init__(self, binary: str):
        self.binary = binary
        self._auth_status: dict[str, Any] | None = None

    def call(self, args: list[str], timeout: int = 45) -> dict[str, Any]:
        return run_json([self.binary] + args, timeout=timeout)

    def auth_status(self) -> dict[str, Any]:
        if self._auth_status is None:
            self._auth_status = self.call(["auth", "status"], timeout=15)
        return self._auth_status

    def current_user(self) -> dict[str, str]:
        status = self.auth_status()
        user_name = str(status.get("userName") or "")
        user_open_id = str(status.get("userOpenId") or "")
        if user_name and user_open_id:
            return {"name": user_name, "open_id": user_open_id}

        data = self.call(["contact", "+get-user"], timeout=20)
        user = data.get("data", {}).get("user", {}) if data.get("ok") else {}
        return {
            "name": str(user.get("name") or user.get("en_name") or user_name or "未知用户"),
            "open_id": str(user.get("open_id") or user_open_id or ""),
        }


def get_week_info(target_date: date) -> tuple[int, date, date]:
    monday = target_date - timedelta(days=target_date.weekday())
    sunday = monday + timedelta(days=6)
    return target_date.isocalendar()[1], monday, sunday


def parse_target_date(raw: str | None) -> date:
    if not raw:
        return date.today()
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError as exc:
        raise SystemExit(f"日期格式错误: {raw}，请使用 YYYY-MM-DD") from exc


def check_token_health(lark: LarkClient, notify_user_id: str, notify_as: str) -> None:
    status = lark.auth_status()
    if not status.get("tokenStatus") == "valid":
        log(f"  lark-cli 授权状态异常: {status.get('tokenStatus') or status.get('_error')}")
        return

    exp = status.get("refreshExpiresAt", "")
    if not exp:
        return
    try:
        refresh_exp = datetime.fromisoformat(exp)
        days_left = (refresh_exp - datetime.now(refresh_exp.tzinfo)).days
    except Exception:
        return
    if days_left > 3 or not notify_user_id:
        return

    message = (
        f"飞书授权即将到期\n\n"
        f"Token 将在 {days_left} 天后失效。\n"
        f"请重新运行: lark-cli auth login --domain im,calendar,drive,docs,contact,minutes --no-wait --json"
    )
    lark.call(
        [
            "im",
            "+messages-send",
            "--user-id",
            notify_user_id,
            "--as",
            notify_as,
            "--markdown",
            message,
        ],
        timeout=20,
    )
    log(f"  Token 剩余 {days_left} 天，已发送预警")


def get_or_create_folder(lark: LarkClient, folder_name: str) -> str:
    result = lark.call(["drive", "files", "list", "--params", '{"folder_token":""}', "--page-all"])
    for item in result.get("data", {}).get("files", []):
        if item.get("name") == folder_name and item.get("type") == "folder":
            log(f"  找到文件夹: {item['token']}")
            return item["token"]

    log(f"  创建文件夹: {folder_name}")
    created = lark.call(["drive", "+create-folder", "--name", folder_name], timeout=45)
    token = (
        created.get("data", {}).get("folder_token")
        or created.get("data", {}).get("token")
        or created.get("folder_token")
    )
    if not token:
        raise SystemExit(f"创建飞书文件夹失败: {created.get('_error') or created}")
    log(f"  文件夹已创建: {token}")
    return str(token)


def get_or_create_weekly_doc(
    lark: LarkClient,
    folder_token: str,
    week_num: int,
    monday: date,
    sunday: date,
) -> tuple[str, str, bool]:
    title = f"第{week_num}周 {monday.strftime('%m/%d')}-{sunday.strftime('%m/%d')}"
    result = lark.call(
        [
            "drive",
            "files",
            "list",
            "--params",
            json.dumps({"folder_token": folder_token}, ensure_ascii=False),
            "--page-all",
        ]
    )
    for item in result.get("data", {}).get("files", []):
        if item.get("name") == title:
            log(f"  找到本周文档: {title} ({item['token']})")
            return str(item["token"]), title, False

    log(f"  创建本周文档: {title}")
    created = lark.call(
        [
            "docs",
            "+create",
            "--api-version",
            "v2",
            "--parent-token",
            folder_token,
            "--title",
            title,
            "--doc-format",
            "markdown",
            "--content",
            f"# {title}\n\n",
        ],
        timeout=60,
    )
    token = (
        created.get("data", {}).get("document", {}).get("document_id")
        or created.get("data", {}).get("document_id")
        or created.get("data", {}).get("token")
    )
    if not token:
        raise SystemExit(f"创建本周文档失败: {created.get('_error') or created}")
    log(f"  文档已创建: {token}")
    return str(token), title, True


def update_doc_section(lark: LarkClient, doc_token: str, heading: str, section_md: str) -> None:
    """Write today's section to the weekly doc via the v2 docs API.

    Re-run case: replace the existing same-day block in place. v2 has no
    `selection-by-title`, so we use markdown `str_replace` with the `前缀...后缀`
    ellipsis to match the whole day block — from the date heading (`## …`) up to
    its trailing `---` separator — and swap in the new block. This stays atomic
    only because the date heading is the block's *only* `##` (inner sections are
    `###`; see the h2-demotion in build_section()).

    First-write case: the pattern isn't found (data.result == "failed"), so fall
    back to `append`. str_replace returns no updated_blocks_count, so success is
    keyed off data.result alone.
    """
    replacement = section_md.rstrip()
    result = lark.call(
        [
            "docs",
            "+update",
            "--api-version",
            "v2",
            "--doc",
            doc_token,
            "--command",
            "str_replace",
            "--doc-format",
            "markdown",
            "--pattern",
            f"{heading}...---",
            "--content",
            replacement,
        ],
        timeout=60,
    )
    data = result.get("data", {}) if isinstance(result, dict) else {}
    replaced = bool(result.get("ok")) and data.get("result") in ("success", "partial_success")
    if replaced:
        log("  已替换现有区段 (v2 str_replace)")
        return

    appended = lark.call(
        [
            "docs",
            "+update",
            "--api-version",
            "v2",
            "--doc",
            doc_token,
            "--command",
            "append",
            "--doc-format",
            "markdown",
            "--content",
            section_md,
        ],
        timeout=60,
    )
    if appended.get("ok"):
        log("  已追加新区段 (v2 append)")
    else:
        log(f"  ⚠️ 写入失败: {appended.get('_error') or appended}")


def send_notification(
    lark: LarkClient,
    doc_token: str,
    target_date: date,
    preview: str,
    notify_user_id: str,
    notify_as: str,
) -> None:
    if not notify_user_id:
        log("  未配置通知用户，跳过飞书通知")
        return
    date_str = target_date.strftime("%Y年%m月%d日")
    doc_url = f"https://feishu.cn/docx/{doc_token}"
    message = f"{date_str} 日报已生成\n\n{preview}\n\n[查看完整日报]({doc_url})"
    result = lark.call(
        [
            "im",
            "+messages-send",
            "--user-id",
            notify_user_id,
            "--as",
            notify_as,
            "--markdown",
            message,
        ],
        timeout=30,
    )
    ok = result.get("ok") or result.get("message_id") or result.get("data", {}).get("message_id")
    log(f"  通知发送: {'成功' if ok else '失败'}")


# ── data collectors ───────────────────────────────────────────────────────────

def get_codex_sessions(target_date: date) -> list[dict[str, str]]:
    db_path = Path.home() / ".codex" / "state_5.sqlite"
    if not db_path.exists():
        return []

    day_start = int(datetime.combine(target_date, datetime.min.time()).timestamp() * 1000)
    day_end = int(datetime.combine(target_date, datetime.max.time()).timestamp() * 1000)
    sessions: list[dict[str, str]] = []
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, title, first_user_message, cwd, created_at_ms, rollout_path
            FROM threads
            WHERE created_at_ms >= ? AND created_at_ms <= ?
            ORDER BY created_at_ms
            """,
            (day_start, day_end),
        )
        for _tid, title, first_msg, cwd, created_at, rollout_path in cursor.fetchall():
            first_msg = first_msg or ""
            display_title = title or first_msg[:80] or "未命名"
            sessions.append(
                {
                    "time": datetime.fromtimestamp(created_at / 1000).strftime("%H:%M") if created_at else "?",
                    "title": display_title,
                    "project": Path(cwd).name if cwd else "unknown",
                    "first_msg": first_msg[:500],
                    "last_summary": read_codex_last_summary(rollout_path),
                }
            )
        conn.close()
    except Exception as exc:
        log(f"  Codex 读取失败: {exc}")
    return sessions


def read_codex_last_summary(rollout_path: str) -> str:
    if not rollout_path or not Path(rollout_path).exists():
        return ""
    try:
        last = ""
        text = Path(rollout_path).read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            if item.get("type") != "response_item":
                continue
            payload = item.get("payload", {})
            if payload.get("role") != "assistant":
                continue
            for content in payload.get("content", []):
                if content.get("type") == "output_text":
                    last = content.get("text", "")
        return last[:500]
    except Exception:
        return ""


def parse_codex_automation(title: str) -> tuple[str | None, str]:
    """Codex automation titles look like:
        'Automation: <name>\nAutomation ID: <id>\n...'
    Returns (automation_id, friendly_name); non-automation titles -> (None, title)."""
    if not title or not title.startswith("Automation:"):
        return None, (title or "未命名")
    name = title.split("\n", 1)[0].replace("Automation:", "").strip()
    aid = name
    for line in title.split("\n"):
        if line.strip().startswith("Automation ID:"):
            aid = line.split(":", 1)[1].strip()
            break
    return aid, name


def group_codex_sessions(
    codex_sessions: list[dict[str, str]],
) -> tuple[list[dict[str, str]], dict[str, dict[str, Any]]]:
    """Split into (interactive, automations). Recurring automation runs are folded
    by automation ID — one entry per task with run count, time span and the most
    recent progress summary — so hourly background loops don't drown out the real
    interactive work."""
    interactive: list[dict[str, str]] = []
    autos: dict[str, dict[str, Any]] = {}
    for session in codex_sessions:
        aid, name = parse_codex_automation(session.get("title", ""))
        if aid is None:
            interactive.append(session)
            continue
        group = autos.get(aid)
        if group is None:
            group = autos[aid] = {
                "name": name,
                "project": session["project"],
                "count": 0,
                "times": [],
                "last_summary": "",
            }
        group["count"] += 1
        group["times"].append(session["time"])
        if session.get("last_summary"):
            group["last_summary"] = session["last_summary"]
    return interactive, autos


def _is_noise_prompt(text: str) -> bool:
    """Pure slash-commands / shell escapes / blank lines carry no work signal —
    drop them from a session's follow-up list (the kickoff line is kept anyway)."""
    text = text.strip()
    if not text:
        return True
    if text.startswith("/") or text.startswith("!"):
        return True
    return False


def get_claude_sessions(target_date: date) -> list[dict[str, Any]]:
    """Group Claude history.jsonl by sessionId and keep the *full* sequence of
    user prompts per session. history.jsonl records only user messages, so the
    prompt arc is the best proxy for "what this session worked on"; keeping all
    of it (not just the first line) stops real deliverables from being collapsed
    into a single vague entry."""
    history_path = Path.home() / ".claude" / "history.jsonl"
    if not history_path.exists():
        return []

    day_start_ms = int(datetime.combine(target_date, datetime.min.time()).timestamp() * 1000)
    day_end_ms = int(datetime.combine(target_date, datetime.max.time()).timestamp() * 1000)
    by_sid: dict[str, dict[str, Any]] = {}
    try:
        for line in history_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            timestamp = entry.get("timestamp", 0)
            if not (day_start_ms <= timestamp <= day_end_ms):
                continue
            display = str(entry.get("display", "") or "").strip()
            if not display:
                continue
            session_id = entry.get("sessionId", "")
            record = by_sid.get(session_id)
            if record is None:
                record = by_sid[session_id] = {
                    "time": datetime.fromtimestamp(timestamp / 1000).strftime("%H:%M"),
                    "project": Path(str(entry.get("project", ""))).name or "unknown",
                    "msgs": [],
                }
            record["msgs"].append(display)
    except Exception:
        return []
    return sorted(by_sid.values(), key=lambda r: r["time"])


def get_feishu_messages(lark: LarkClient, target_date: date, timezone_offset: str) -> list[dict[str, Any]]:
    date_str = target_date.strftime("%Y-%m-%d")
    result = lark.call(
        [
            "im",
            "+messages-search",
            "--start",
            f"{date_str}T00:00:00{timezone_offset}",
            "--end",
            f"{date_str}T23:59:59{timezone_offset}",
            "--page-all",
            "--exclude-sender-type",
            "bot",
        ],
        timeout=60,
    )
    return result.get("data", {}).get("messages", []) if result.get("ok") else []


def get_feishu_calendar(lark: LarkClient, target_date: date, timezone_offset: str) -> list[dict[str, Any]]:
    date_str = target_date.strftime("%Y-%m-%d")
    result = lark.call(
        [
            "calendar",
            "+agenda",
            "--start",
            f"{date_str}T00:00:00{timezone_offset}",
            "--end",
            f"{date_str}T23:59:59{timezone_offset}",
        ],
        timeout=45,
    )
    if not result.get("ok"):
        return []
    data = result.get("data", [])
    return data if isinstance(data, list) else data.get("items", [])


def get_meeting_minutes(lark: LarkClient, target_date: date) -> list[dict[str, Any]]:
    """Search Feishu minutes (妙记) for the day as both owner and participant,
    deduped by token. The +search payload puts the title in `display_info` and
    metadata (owner/start/duration/keywords) in `description` — reading a
    non-existent `title` field always came up empty."""
    date_str = target_date.strftime("%Y-%m-%d")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for role in ("--owner-ids", "--participant-ids"):
        result = lark.call(
            ["minutes", "+search", "--start", date_str, "--end", date_str, role, "me", "--page-size", "30"],
            timeout=45,
        )
        if not result.get("ok"):
            continue
        for item in result.get("data", {}).get("items", []):
            token = item.get("token")
            if token and token in seen:
                continue
            if token:
                seen.add(token)
            items.append(item)
    return items


def get_chrome_history(target_date: date, profile: str, skip_domains: set[str]) -> dict[str, dict[str, Any]]:
    history_path = Path.home() / "Library" / "Application Support" / "Google" / "Chrome" / profile / "History"
    if not history_path.exists():
        return {}

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        shutil.copy2(str(history_path), tmp_path)
        conn = sqlite3.connect(tmp_path)
        cursor = conn.cursor()
        chrome_epoch = 11_644_473_600_000_000
        start = int(datetime.combine(target_date, datetime.min.time()).timestamp() * 1_000_000) + chrome_epoch
        end = int(datetime.combine(target_date, datetime.max.time()).timestamp() * 1_000_000) + chrome_epoch
        cursor.execute(
            """
            SELECT url, title, visit_count
            FROM urls
            WHERE last_visit_time >= ? AND last_visit_time <= ?
              AND url NOT LIKE 'chrome://%'
              AND url NOT LIKE 'chrome-extension://%'
              AND url NOT LIKE 'about:%'
            ORDER BY last_visit_time DESC
            """,
            (start, end),
        )
        domains: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "titles": []})
        for url, title, visit_count in cursor.fetchall():
            try:
                domain = urlparse(url).netloc
                if not domain or any(skip in domain for skip in skip_domains):
                    continue
                domains[domain]["count"] += int(visit_count or 0)
                if title and title not in domains[domain]["titles"]:
                    domains[domain]["titles"].append(title)
            except Exception:
                continue
        conn.close()
        return dict(sorted(domains.items(), key=lambda item: -item[1]["count"]))
    except Exception as exc:
        return {"_error": {"count": 0, "titles": [str(exc)]}}
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


# ── prompt building ────────────────────────────────────────────────────────────

def build_prompt(
    target_date: date,
    codex_sessions: list[dict[str, str]],
    claude_sessions: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    calendar_events: list[dict[str, Any]],
    minutes_items: list[dict[str, Any]],
    chrome: dict[str, dict[str, Any]],
) -> str:
    date_str = target_date.strftime("%Y年%m月%d日")
    lines = [f"以下是 {date_str} 的原始工作记录，请整理成日报。\n"]

    # Codex — interactive listed individually, hourly automations folded by ID.
    codex_interactive, codex_autos = group_codex_sessions(codex_sessions)
    if codex_interactive or codex_autos:
        lines.append(f"## Codex 会话（共 {len(codex_sessions)} 个原始 session，逐个记录，不要遗漏）")
        for idx, session in enumerate(codex_interactive, 1):
            title = (session.get("title") or "").split("\n", 1)[0]
            lines.append(f"### Codex交互{idx}｜{session['time']}｜项目[{session['project']}]｜{title}")
            if session.get("first_msg"):
                lines.append(f"  用户指令：{session['first_msg'][:400]}")
            if session.get("last_summary"):
                lines.append(f"  完成/产出：{session['last_summary'][:400]}")
        for _aid, group in codex_autos.items():
            span = f"{group['times'][0]}–{group['times'][-1]}" if group["times"] else ""
            lines.append(
                f"### Codex自动化｜{group['name']}｜项目[{group['project']}]｜当天运行 {group['count']} 次（{span}）"
            )
            if group["last_summary"]:
                lines.append(f"  最新进展：{group['last_summary'][:400]}")
        lines.append("")

    # Claude Code — every session with its full instruction arc.
    if claude_sessions:
        lines.append(f"## Claude Code 会话（共 {len(claude_sessions)} 个 session，逐个记录，不要遗漏）")
        for idx, session in enumerate(claude_sessions, 1):
            msgs = session["msgs"]
            substantive = [m for m in msgs if not _is_noise_prompt(m)]
            if substantive:
                opener, followups = substantive[0], substantive[1:]
            else:
                opener, followups = msgs[0], []
            lines.append(f"### Claude会话{idx}｜{session['time']}｜项目[{session['project']}]｜共 {len(msgs)} 条指令")
            lines.append(f"  开场指令：{opener[:300]}")
            if followups:
                lines.append("  后续指令（按时间）：")
                for msg in followups[:12]:
                    lines.append(f"    · {msg[:120]}")
                if len(followups) > 12:
                    lines.append(f"    · …（另有 {len(followups) - 12} 条同会话指令）")
        lines.append("")

    # Calendar — time + video-conference flag.
    if calendar_events:
        lines.append("## 今日飞书会议 / 日程")
        for event in calendar_events:
            summary = event.get("summary") or event.get("title") or "未知"
            start_time = event.get("start_time", {})
            clock = ""
            if isinstance(start_time, dict):
                dt = start_time.get("datetime", "")
                clock = dt[11:16] if len(dt) >= 16 else ""
            is_vc = bool((event.get("vchat") or {}).get("meeting_url"))
            lines.append(f"- {clock} {summary}".strip() + ("（视频会议）" if is_vc else ""))
        lines.append("")

    # Minutes (妙记) — title from display_info, metadata from description.
    if minutes_items:
        lines.append("## 会议纪要（妙记）")
        for item in minutes_items:
            title = item.get("display_info") or item.get("title") or "未命名会议"
            desc = (item.get("description") or "").replace("<b>", "").replace("</b>", "")
            url = item.get("app_link") or item.get("url") or ""
            lines.append(f"- {title}" + (f"（{url}）" if url else ""))
            if desc:
                lines.append(f"  {desc[:200]}")
        lines.append("")

    if messages:
        chat_counts: dict[str, int] = defaultdict(int)
        for message in messages:
            chat_counts[str(message.get("chat_id", ""))] += 1
        lines.append("## 飞书沟通")
        lines.append(f"参与了 {len(chat_counts)} 个会话，共 {len(messages)} 条消息。")
        lines.append("")

    work_domains = [(domain, value) for domain, value in list(chrome.items())[:15] if not domain.startswith("_")]
    if work_domains:
        lines.append("## 浏览记录（工作相关）")
        for domain, value in work_domains[:10]:
            top_title = value["titles"][0] if value["titles"] else ""
            suffix = f" - {top_title[:50]}" if top_title else ""
            lines.append(f"- {domain} ({value['count']}次){suffix}")
        lines.append("")

    lines.append(
        """---
请按以下格式输出日报（不需要 markdown 代码块）。

**标题层级要求（很重要）：下面四个区段标题一律用三级标题 `###`，不要用 `##`。`##` 只保留给外层的日期标题，正文里出现 `##` 会破坏文档结构。**

**最重要的内容规则：上面列出的每一个 Claude Code session 和每一个 Codex 会话，都必须在「AI 会话明细」里出现，逐个记录，不得遗漏、不得把多个 session 笼统合并成一句。** 即使某个 session 看起来很小，也要写一行说明它做了什么。只有 session *内部* 的环境配置 / 授权 / 纯闲聊可以略去，session 本身不能略去。

### 今日工作总结
2-3 句话概括今天做了什么、有哪些关键产出。

### 主要项目进展
按项目分组，列出具体完成的事情和产出（产出了飞书文档/PR/commit/分支/报告就写出来，能带链接带链接）。

### AI 会话明细（逐个 session，禁止遗漏）
分「Claude Code」和「Codex」两个子标题（用加粗或四级标题，不要用 ##）：
- 每个 session 写一条：`时间 · 项目 · 做了什么 / 产出了什么`。要结合该 session 的全部指令判断它的真实目标与结果，而不是只看开场白。
- 若同一 session 有多步推进（如先规划再细化排期再分工），要体现这条主线。
- Codex 的定时自动化任务已按任务归并，按「任务名 · 当天运行N次 · 最新进展」写一条即可。

### 沟通 & 会议
- 逐条列出今日飞书会议（时间 + 主题，标注是否视频会议）；有会议纪要则概述要点。
- 末尾一句话概述飞书沟通量（多少会话 / 多少消息）。

注意：略去环境配置、授权等背景噪音，但「有实质工作的 session 必须逐个出现」优先级最高。"""
    )
    return "\n".join(lines)


def summarize_with_codex(codex_cli: str, prompt: str) -> tuple[str, str]:
    if not codex_cli:
        return "", "未找到 codex CLI"

    log("  调用 Codex 生成总结...")
    APP_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix="codex-summary-", suffix=".md", delete=False) as output:
        output_path = output.name
    try:
        result = subprocess.run(
            [
                codex_cli,
                "exec",
                "--skip-git-repo-check",
                "--ephemeral",
                "--output-last-message",
                output_path,
                "-",
            ],
            input=prompt,
            capture_output=True,
            text=True,
            cwd=str(APP_DIR),
            timeout=180,
        )
        summary = Path(output_path).read_text(encoding="utf-8", errors="replace").strip()
        if result.returncode == 0 and summary:
            return summary, ""
        detail = result.stderr.strip() or result.stdout.strip() or f"{command_preview([codex_cli, 'exec'])} exited {result.returncode}"
        return "", detail[:800]
    except subprocess.TimeoutExpired:
        return "", "Codex 生成总结超时"
    except Exception as exc:
        return "", f"Codex 调用异常: {exc}"
    finally:
        try:
            os.unlink(output_path)
        except Exception:
            pass


def summarize_with_claude(claude_cli: str, prompt: str) -> tuple[str, str]:
    if not claude_cli:
        return "", "未找到 claude CLI"

    log("  Codex 不可用，调用 Claude 备用总结...")
    try:
        result = subprocess.run(
            [claude_cli, "-p", prompt, "--output-format", "text"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip(), ""
        detail = result.stderr.strip() or result.stdout.strip() or f"{command_preview([claude_cli, '-p'])} exited {result.returncode}"
        return "", detail[:800]
    except subprocess.TimeoutExpired:
        return "", "Claude 生成总结超时"
    except Exception as exc:
        return "", f"Claude 调用异常: {exc}"


def ai_unavailable_message(errors: list[str]) -> str:
    details = "\n".join(f"- {error}" for error in errors if error)
    if not details:
        details = "- 未找到可用的 Codex 或 Claude CLI"
    return (
        "无法生成 AI 日报总结：Codex 不可用，Claude 备用也不可用。\n\n"
        f"{details}\n\n"
        "建议处理方式：\n"
        "1. 优先开通并登录 Codex：安装/打开 Codex，运行 `codex login status` 检查登录状态；未登录时运行 `codex login`。\n"
        "2. 确认命令行可用：运行 `codex exec --skip-git-repo-check --ephemeral \"只输出 OK\"`。\n"
        "3. 如果暂时无法使用 Codex，可以安装并登录 Claude Code CLI 作为备用。\n"
        "4. 如果只想生成基础版原始记录，可显式加 `--no-ai`。"
    )


def summarize_with_ai(codex_cli: str, claude_cli: str, prompt: str, enabled: bool) -> str:
    if not enabled:
        return ""

    errors: list[str] = []
    summary, error = summarize_with_codex(codex_cli, prompt)
    if summary:
        return summary
    if error:
        log(f"  Codex 总结失败: {error[:200]}")
        errors.append(f"Codex: {error}")

    summary, error = summarize_with_claude(claude_cli, prompt)
    if summary:
        return summary
    if error:
        log(f"  Claude 备用失败: {error[:200]}")
        errors.append(f"Claude: {error}")

    raise SystemExit(ai_unavailable_message(errors))


def fallback_format(
    codex_sessions: list[dict[str, str]],
    claude_sessions: list[dict[str, Any]],
    calendar_events: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    chrome: dict[str, dict[str, Any]],
) -> str:
    lines = ["### 今日工作总结", "AI 总结不可用，以下为自动整理的原始记录。", ""]
    if codex_sessions:
        interactive, autos = group_codex_sessions(codex_sessions)
        lines += ["### Codex 会话"]
        for session in interactive:
            title = (session.get("title") or "").split("\n", 1)[0]
            lines.append(f"- {session['time']} [{session['project']}] {title}")
        for _aid, group in autos.items():
            lines.append(f"- [自动化] {group['name']} [{group['project']}] ×{group['count']} 次")
        lines.append("")
    if claude_sessions:
        lines += ["### Claude Code 会话"]
        for session in claude_sessions:
            opener = next((m for m in session["msgs"] if not _is_noise_prompt(m)), session["msgs"][0])
            lines.append(f"- {session['time']} [{session['project']}] {opener[:100]}（共 {len(session['msgs'])} 条指令）")
        lines.append("")
    if calendar_events:
        lines += ["### 日程"]
        for event in calendar_events:
            lines.append(f"- {event.get('summary') or event.get('title') or '未知'}")
        lines.append("")
    if messages:
        chat_counts = {str(message.get("chat_id", "")) for message in messages}
        lines += ["### 飞书沟通", f"- 参与 {len(chat_counts)} 个会话，共 {len(messages)} 条消息。", ""]
    domains = [(domain, value) for domain, value in chrome.items() if not domain.startswith("_")][:10]
    if domains:
        lines += ["### 浏览记录"]
        for domain, value in domains:
            lines.append(f"- {domain}: {value['count']} 次")
    return "\n".join(lines).strip()


def normalize_summary(summary: str) -> str:
    """Keep the day block atomic for re-runs:
      • demote any stray h2 -> h3 (the date heading is the block's only `##`)
      • drop stray horizontal rules so the only `---` in the block is the
        trailing separator the str_replace pattern keys off.
    """
    def fix(line: str) -> str:
        if line.strip() in ("---", "***", "___"):
            return ""
        return ("###" + line[2:]) if line.startswith("## ") else line

    return "\n".join(fix(line) for line in summary.split("\n"))


def build_section(target_date: date, summary: str) -> tuple[str, str]:
    """Return (date_heading, section_md). The date heading is the block's only
    `##`; the block ends with a single trailing `---` separator."""
    summary = normalize_summary(summary)
    weekday = WEEKDAY_CN[target_date.weekday()]
    date_heading = f"## {target_date.strftime('%Y年%m月%d日')}（周{weekday}）"
    section_md = f"{date_heading}\n\n{summary}\n\n---\n\n"
    return date_heading, section_md


def collect_data(
    lark: LarkClient,
    target_date: date,
    config: dict[str, Any],
) -> dict[str, Any]:
    log("采集 Codex 会话...")
    codex_sessions = get_codex_sessions(target_date)
    log(f"  -> {len(codex_sessions)} 个")

    log("采集 Claude 对话...")
    claude_sessions = get_claude_sessions(target_date)
    log(f"  -> {len(claude_sessions)} 个 session")

    log("采集飞书日历...")
    calendar_events = get_feishu_calendar(lark, target_date, str(config["timezone_offset"]))
    log(f"  -> {len(calendar_events)} 个")

    log("采集飞书会议纪要...")
    minutes_items = get_meeting_minutes(lark, target_date)
    log(f"  -> {len(minutes_items)} 个")

    log("采集飞书消息...")
    messages = get_feishu_messages(lark, target_date, str(config["timezone_offset"]))
    log(f"  -> {len(messages)} 条")

    log("采集 Chrome 浏览历史...")
    chrome = get_chrome_history(
        target_date,
        str(config["chrome_profile"]),
        set(config.get("skip_domains") or DEFAULT_SKIP_DOMAINS),
    )
    log(f"  -> {len([key for key in chrome if not key.startswith('_')])} 个域名")

    return {
        "codex_sessions": codex_sessions,
        "claude_sessions": claude_sessions,
        "calendar_events": calendar_events,
        "minutes_items": minutes_items,
        "messages": messages,
        "chrome": chrome,
    }


def cli_status(binary: str, args: list[str], timeout: int = 10) -> str:
    if not binary:
        return "未找到"
    try:
        result = subprocess.run([binary] + args, capture_output=True, text=True, timeout=timeout)
    except Exception as exc:
        return f"不可用 ({exc})"
    output = (result.stdout or result.stderr).strip().splitlines()
    detail = output[0] if output else f"exit {result.returncode}"
    return detail if result.returncode == 0 else f"不可用 ({detail})"


def install_check(config: dict[str, Any], lark_cli: str, codex_cli: str, claude_cli: str) -> int:
    print("Daily Report Skill 安装检查")
    print(f"- Python: {sys.version.split()[0]}")
    print(f"- lark-cli: {lark_cli or '未找到'}")
    print(f"- Codex CLI: {codex_cli or '未找到'}")
    print(f"- Codex 登录: {cli_status(codex_cli, ['login', 'status'])}")
    print(f"- Claude CLI 备用: {claude_cli or '未找到'}")
    if claude_cli:
        print(f"- Claude 版本: {cli_status(claude_cli, ['--version'])}")
    print(f"- 配置文件: {DEFAULT_CONFIG_PATH if DEFAULT_CONFIG_PATH.exists() else '未创建'}")

    if config.get("ai_enabled", True) and not codex_cli and not claude_cli:
        print("\n缺少 AI 能力：请优先安装/登录 Codex；Claude Code CLI 可作为备用。")
        return 1

    if not lark_cli:
        print("\n缺少 lark-cli。请先安装 @larksuite/cli 并完成授权。")
        return 1

    lark = LarkClient(lark_cli)
    status = lark.auth_status()
    print(f"- 飞书授权: {status.get('tokenStatus') or status.get('_error')}")
    print(f"- 飞书用户: {status.get('userName') or '未知'}")
    if status.get("tokenStatus") != "valid":
        print("\n授权不可用。请运行 lark-cli auth login。")
        return 1

    required = ["im", "calendar", "drive", "docs", "contact", "minutes"]
    scope_text = str(status.get("scope") or "")
    missing_hints = [domain for domain in required if domain not in scope_text]
    if missing_hints:
        print(f"- 权限提示: 可能缺少 {', '.join(missing_hints)} 相关权限")
    else:
        print("- 权限提示: 基础域名权限看起来齐全")

    notify_user = str(config.get("notify_user_id") or status.get("userOpenId") or "")
    print(f"- 通知用户 open_id: {notify_user or '未配置'}")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a Feishu/Lark daily report.")
    parser.add_argument("positional_date", nargs="?", help="Report date, YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--date", dest="date_value", help="Report date, YYYY-MM-DD. Overrides positional date.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Config JSON path.")
    parser.add_argument("--init-config", action="store_true", help="Create a default config file and exit.")
    parser.add_argument("--install-check", action="store_true", help="Check dependencies and Feishu auth.")
    parser.add_argument("--dry-run", action="store_true", help="Collect data and print markdown without writing Feishu.")
    parser.add_argument("--no-ai", action="store_true", help="Do not call Codex or Claude; use basic fallback formatting.")
    parser.add_argument("--no-notify", action="store_true", help="Do not send Feishu notification.")
    parser.add_argument("--folder-name", help="Feishu folder name. Defaults to config value.")
    parser.add_argument("--notify-user-id", help="Feishu open_id to notify. Defaults to current user.")
    parser.add_argument("--lark-cli", help="Path to lark-cli.")
    parser.add_argument("--codex-cli", help="Path to codex CLI.")
    parser.add_argument("--claude-cli", help="Path to Claude Code CLI fallback.")
    parser.add_argument("--ai-cli", help="Deprecated alias for --claude-cli.")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    config_path = Path(args.config).expanduser()

    if args.init_config:
        save_default_config(config_path)
        return 0

    config = load_config(config_path)
    if args.folder_name:
        config["folder_name"] = args.folder_name
    if args.no_notify:
        config["send_notification"] = False
    if args.notify_user_id:
        config["notify_user_id"] = args.notify_user_id
    if args.no_ai:
        config["ai_enabled"] = False
    if args.lark_cli:
        config["lark_cli"] = args.lark_cli
    if args.codex_cli:
        config["codex_cli"] = args.codex_cli
    if args.claude_cli:
        config["claude_cli"] = args.claude_cli
    if args.ai_cli:
        config["claude_cli"] = args.ai_cli

    lark_cli = find_binary(str(config.get("lark_cli") or ""), "lark-cli", ["/opt/homebrew/bin/lark-cli"])
    codex_cli = find_binary(
        str(config.get("codex_cli") or ""),
        "codex",
        ["/Applications/Codex.app/Contents/Resources/codex"],
    )
    claude_cli = find_binary(
        str(config.get("claude_cli") or config.get("ai_cli") or ""),
        "claude",
        [str(Path.home() / ".local" / "bin" / "claude")],
    )

    if args.install_check:
        return install_check(config, lark_cli, codex_cli, claude_cli)
    if not lark_cli:
        raise SystemExit("未找到 lark-cli。请先安装 @larksuite/cli 并完成飞书授权。")

    target_date = parse_target_date(args.date_value or args.positional_date)
    lark = LarkClient(lark_cli)
    user = lark.current_user()
    notify_user_id = str(config.get("notify_user_id") or user.get("open_id") or "")
    notify_as = str(config.get("notify_as") or "user")

    log(f"===== 开始生成 {target_date} 日报 =====")
    check_token_health(lark, notify_user_id, notify_as)

    week_num, monday, sunday = get_week_info(target_date)
    log(f"第 {week_num} 周  {monday} ~ {sunday}")

    data = collect_data(lark, target_date, config)
    prompt = build_prompt(target_date, **data)
    summary = summarize_with_ai(codex_cli, claude_cli, prompt, bool(config.get("ai_enabled", True)))
    if not summary:
        summary = fallback_format(
            data["codex_sessions"],
            data["claude_sessions"],
            data["calendar_events"],
            data["messages"],
            data["chrome"],
        )

    date_heading, section_md = build_section(target_date, summary)

    if args.dry_run:
        print("\n" + section_md)
        log("===== dry-run 完成，未写入飞书 =====")
        return 0

    log("获取/创建飞书文件夹...")
    folder_token = get_or_create_folder(lark, str(config["folder_name"]))

    log("获取/创建本周文档...")
    doc_token, _doc_title, _created = get_or_create_weekly_doc(lark, folder_token, week_num, monday, sunday)

    log("写入飞书文档...")
    update_doc_section(lark, doc_token, date_heading, section_md)

    first_line = next((line for line in summary.splitlines() if line.strip() and not line.startswith("#")), "已生成")
    if config.get("send_notification", True):
        log("发送飞书通知...")
        send_notification(lark, doc_token, target_date, first_line, notify_user_id, notify_as)
    else:
        log("已按配置跳过飞书通知")

    log(f"===== 完成！文档: https://feishu.cn/docx/{doc_token} =====")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
