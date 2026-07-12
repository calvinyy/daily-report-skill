#!/usr/bin/env python3
"""Morning briefing generator for the daily-report skill.

Assembles a 10:00 morning briefing for the user from three sources:
  1. Yesterday's daily report (link + optional recap) written by this skill.
  2. Today's TODOs — the user's Feishu todo note plus the local work digest.
  3. Time-bound appointments from the user's Feishu calendar.

Design goals: self-contained (only depends on `lark-cli` and the local digest
files), safe to run headless from cron, and prints Markdown to stdout so the
caller (cc-connect cron / a chat bot) can deliver it verbatim.

Config is read from the same config file as generate_daily_report.py; the
morning-briefing specifics live under a `morning_briefing` key, e.g.

    "morning_briefing": {
        "todo_doc_token": "CHgcd78UFohk66xACkUcUkNPnb9",
        "digest_dir": "~/Riemann/工作整理"
    }
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import date, datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from typing import Any

# The user lives on Beijing time (UTC+8); the host clock may be elsewhere, so
# always derive "today" and the weekday from Beijing wall-clock time.
BEIJING = timezone(timedelta(hours=8))

DEFAULT_TODO_DOC_TOKEN = "CHgcd78UFohk66xACkUcUkNPnb9"
DEFAULT_DIGEST_DIR = "~/Riemann/工作整理"
CONFIG_PATHS = [
    Path("~/.codex/skills/daily-report/config.json").expanduser(),
    Path("~/.config/daily-report/config.json").expanduser(),
]


def _lark(args: list[str], timeout: int = 45) -> dict[str, Any]:
    try:
        proc = subprocess.run(["lark-cli", *args], capture_output=True, text=True, timeout=timeout)
    except Exception as exc:  # pragma: no cover - environment dependent
        return {"ok": False, "_error": str(exc)}
    raw = (proc.stdout or "").strip()
    if not raw:
        return {"ok": False, "_error": (proc.stderr or "").strip() or "empty output"}
    try:
        return json.loads(raw)
    except Exception:
        return {"ok": False, "_error": raw[:300]}


def _load_config() -> dict[str, Any]:
    for path in CONFIG_PATHS:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
    return {}


# --- TODO note (Feishu docx) -------------------------------------------------

def fetch_todo_sections(doc_token: str) -> list[tuple[str, list[tuple[str, bool]]]]:
    """Return [(date_heading, [(item_text, struck)])] for the note's dated
    sections, most recent first. Struck-through (<del>) items are marked done."""
    result = _lark(["docs", "+fetch", "--doc", doc_token, "--api-version", "v2"])
    content = ""
    if isinstance(result.get("data"), dict):
        content = result["data"].get("document", {}).get("content", "")
    content = content or result.get("document", {}).get("content", "")
    if not content:
        return []
    # Split on date-like headings (H1/H2/H3) such as "7月9日" / "2026-07-09".
    heading_re = re.compile(r"<h[123]>(.*?)</h[123]>", re.S)
    date_re = re.compile(r"(\d{1,2}\s*月\s*\d{1,2}\s*日|\d{4}-\d{2}-\d{2})")
    sections: list[tuple[str, list[tuple[str, bool]]]] = []
    parts = heading_re.split(content)
    # parts = [pre, heading1, body1, heading2, body2, ...]
    for i in range(1, len(parts) - 1, 2):
        heading = _strip_tags(parts[i]).strip()
        body = parts[i + 1]
        if not date_re.search(heading):
            continue
        items: list[tuple[str, bool]] = []
        for li in re.findall(r"<li>(.*?)</li>", body, re.S):
            struck = "<del>" in li or "<s>" in li
            text = _strip_tags(li).strip()
            if text:
                items.append((text, struck))
        if items:
            sections.append((heading, items))
    return list(reversed(sections))  # most-recent date first (doc is chronological)


def _strip_tags(html: str) -> str:
    text = re.sub(r"<[^>]+>", "", html)
    return unescape(" ".join(text.split()))


def _prev_workday(d: date) -> date:
    d -= timedelta(days=1)
    while d.weekday() >= 5:  # skip Sat(5)/Sun(6)
        d -= timedelta(days=1)
    return d


# --- Calendar ----------------------------------------------------------------

def fetch_today_events() -> list[dict[str, str]]:
    result = _lark(["calendar", "+agenda"])
    data = result.get("data")
    events: list[dict[str, str]] = []
    if not isinstance(data, list):
        return events
    for ev in data:
        start = _fmt_time(ev.get("start_time", {}))
        end = _fmt_time(ev.get("end_time", {}))
        summary = ev.get("summary") or "(无标题日程)"
        loc = (ev.get("location") or {}).get("name") or ""
        vc = (ev.get("vchat") or {}).get("meeting_url") or ""
        rsvp = ev.get("self_rsvp_status") or ""
        events.append({"start": start, "end": end, "summary": summary, "location": loc, "vc": vc, "rsvp": rsvp})
    events.sort(key=lambda e: e["start"])
    return events


def _fmt_time(t: dict[str, Any]) -> str:
    raw = t.get("datetime") or ""
    try:
        dt = datetime.fromisoformat(raw)
        return dt.strftime("%H:%M")
    except Exception:
        return raw[:16]


# --- Local work digest -------------------------------------------------------

def fetch_pending_from_digest(digest_dir: Path, today: date) -> list[str]:
    month_file = digest_dir / "日常事项" / f"{today:%Y-%m}.md"
    pending: list[str] = []
    if month_file.exists():
        for line in month_file.read_text(encoding="utf-8").splitlines():
            if ("🔴" in line or "🟡" in line) and line.strip().startswith("#"):
                pending.append(line.lstrip("# ").strip())
    overview = digest_dir / "工作整理总览.md"
    if overview.exists():
        capture = False
        for line in overview.read_text(encoding="utf-8").splitlines():
            if line.startswith("## ") and "待办" in line:
                capture = True
                continue
            if capture and line.startswith("## "):
                break
            if capture and line.strip().startswith("- "):
                pending.append(line.strip()[2:].strip())
    return pending


# --- Assembly ----------------------------------------------------------------

def build_briefing(today: date, cfg: dict[str, Any]) -> str:
    mb = cfg.get("morning_briefing", {}) if isinstance(cfg.get("morning_briefing"), dict) else {}
    doc_token = mb.get("todo_doc_token") or DEFAULT_TODO_DOC_TOKEN
    digest_dir = Path(mb.get("digest_dir") or DEFAULT_DIGEST_DIR).expanduser()
    yesterday = _prev_workday(today)  # skip back over the weekend (Mon → Fri)

    out: list[str] = [f"# ☀️ 晨间简报 · {today:%Y-%m-%d}（周{'一二三四五六日'[today.weekday()]}）", ""]

    # 1. Time reminders
    events = fetch_today_events()
    out.append("## 📅 今日时间提醒")
    if events:
        for e in events:
            line = f"- **{e['start']}–{e['end']}　{e['summary']}**"
            extras = [x for x in (e["location"], (f"线上 {e['vc']}" if e["vc"] else "")) if x]
            if extras:
                line += "（" + " · ".join(extras) + "）"
            if e["rsvp"] == "needs_action":
                line += " ⚠️ 未回复邀请"
            out.append(line)
    else:
        out.append("- 今日暂无日历日程。")
    out.append("")

    # 2. Today's TODOs
    out.append("## ✅ 今日 TODO")
    sections = fetch_todo_sections(doc_token)
    open_items: list[str] = []
    for heading, items in sections[:3]:  # latest 3 dated sections
        live = [t for t, struck in items if not struck]
        if live:
            open_items.append(f"- 【{heading}】" + "；".join(live))
    out.extend(open_items or ["- （未从飞书 todo 笔记读到未完成项）"])
    out.append("")

    # 3. Follow-ups from the local digest
    pending = fetch_pending_from_digest(digest_dir, today)
    if pending:
        out.append("## 👥 待跟进（别人找你 / 本地待办）")
        seen: set[str] = set()
        for p in pending:
            if p not in seen:
                out.append(f"- {p}")
                seen.add(p)
        out.append("")

    # 4. Yesterday pointer
    out.append("## 📄 昨日日报")
    out.append(f"- 昨天（{yesterday:%Y-%m-%d}）的工作日报见「日报」飞书文件夹；如需完整回顾可运行日报生成。")

    return "\n".join(out).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the morning briefing.")
    parser.add_argument("--date", help="ISO date (default: today, Beijing time)")
    parser.add_argument(
        "--include-weekend",
        action="store_true",
        help="Also generate on Sat/Sun (by default the briefing is weekday-only).",
    )
    args = parser.parse_args()
    today = date.fromisoformat(args.date) if args.date else datetime.now(BEIJING).date()
    if today.weekday() >= 5 and not args.include_weekend:
        # Weekday-only briefing: emit nothing so a cron/bot has nothing to deliver.
        return 0
    print(build_briefing(today, _load_config()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
