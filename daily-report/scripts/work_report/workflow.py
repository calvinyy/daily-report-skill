from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from work_report.collector_registry import collect_activity
from work_report.config import LOG_PATH
from work_report.lark_client import LarkClient
from work_report.lark_writer import report_doc_url, write_report
from work_report.models import ActivityBundle, ReportKind, SourceRecord, build_date_window
from work_report.summarizer import summarize


def log(message: str) -> None:
    print(message)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] {message}\n")
    except OSError:
        pass


def bundle_to_jsonable(bundle: ActivityBundle) -> dict[str, Any]:
    return {
        "collection_status": dict(sorted(bundle.collection_status.items())),
        "records": [_record_to_jsonable(record) for record in bundle.sorted_records()],
    }


def dry_run_markdown(target_date: date, kind: ReportKind, summary: str, bundle: ActivityBundle) -> str:
    window = build_date_window(target_date, kind)
    body = summary.strip()
    if body.startswith("# "):
        return body + "\n"
    return "\n".join([f"# {window.section_heading}", "", body]).rstrip() + "\n"


def send_notification(
    lark: Any,
    doc_token: str,
    target_date: date,
    summary: str,
    notify_user_id: str,
    notify_as: str,
) -> bool:
    """Returns True if the DM was sent (or there was nothing to send), False on a
    delivery failure. Callers fail the run on False so the guard does NOT mark the
    day done and retries — otherwise a successful doc write + failed notify is
    silently lost (the 2026-06-03 failure mode the monolith's 06-04 fix killed)."""
    if not notify_user_id:
        return True
    message = "\n".join(
        [
            f"工作报告已生成：{target_date.isoformat()}",
            report_doc_url(doc_token),
            "",
            _notification_excerpt(summary),
        ]
    ).strip()
    args = ["im", "+messages-send", "--user-id", notify_user_id, "--markdown", message]
    if notify_as:
        args.extend(["--as", notify_as])
    result = lark.call(args, timeout=30)
    if result.get("ok") is False:
        log(f"通知发送失败: {result.get('_error') or result}")
        return False
    log("通知发送成功")
    return True


def run_install_check(config: dict[str, Any], lark_cli: str, codex_cli: str, claude_cli: str) -> int:
    ok = True
    if lark_cli:
        log(f"lark-cli: {lark_cli}")
        auth = LarkClient(lark_cli).auth_status()
        if auth.get("ok") is False:
            log(f"lark auth: failed ({auth.get('_error') or auth})")
            ok = False
        else:
            user = auth.get("userName") or auth.get("userOpenId") or "authenticated"
            log(f"lark auth: ok ({user})")
    else:
        log("lark-cli: missing")
        ok = False

    ai_enabled = bool(config.get("ai_enabled", True))
    if not ai_enabled:
        log("AI summary: disabled")
    elif codex_cli:
        log(f"codex CLI: {codex_cli}")
    elif claude_cli:
        log(f"Claude CLI: {claude_cli}")
    else:
        log("AI summary: no codex or claude CLI found; fallback renderer will be used")

    return 0 if ok else 1


def run_workflow(
    target_date: date,
    kind: ReportKind,
    config: dict[str, Any],
    binaries: dict[str, str],
    dry_run: bool,
) -> int:
    window = build_date_window(target_date, kind)
    lark_cli = binaries.get("lark") or ""
    if not dry_run and not lark_cli:
        log("lark-cli: missing; cannot write Feishu report")
        return 1

    lark = LarkClient(lark_cli) if lark_cli else None
    log(f"采集范围: {window.label}")
    bundle = collect_activity(window, config, lark)
    _write_raw_bundle(config.get("include_raw_bundle_path"), bundle)

    summary = summarize(window, bundle, config=config, binaries=binaries, log=log)
    if dry_run:
        print(dry_run_markdown(target_date, kind, summary, bundle), end="")
        return 0

    assert lark is not None
    current_user = lark.current_user()
    editor = current_user.get("name") or "未知用户"
    result = write_report(lark, window, summary, config, editor=editor)
    log(f"已写入飞书文档: {result.url}")

    if config.get("send_notification", True):
        notify_user_id = str(config.get("notify_user_id") or current_user.get("open_id") or "")
        notified = send_notification(
            lark=lark,
            doc_token=result.doc_token,
            target_date=target_date,
            summary=summary,
            notify_user_id=notify_user_id,
            notify_as=str(config.get("notify_as") or "user"),
        )
        if not notified:
            # Doc is written but the user wasn't told — fail so the guard retries
            # (re-run is idempotent: it overwrites the same day's doc).
            return 1

    # After a daily report, fill the shared team tracking sheet. Never let this
    # fail the run — the report is already written and delivered.
    if window.kind == ReportKind.DAILY:
        try:
            from work_report.team_sheet import fill_team_sheet

            fill_team_sheet(lark, config, window.target_date, summary, log=log)
        except Exception as exc:  # pragma: no cover - defensive
            log(f"团队表格填写异常(不影响日报): {exc}")
    return 0


def _record_to_jsonable(record: SourceRecord) -> dict[str, Any]:
    return {
        "source": record.source,
        "title": record.title,
        "content": record.content,
        "source_detail": record.source_detail,
        "timestamp": record.timestamp.isoformat() if record.timestamp else None,
        "url": record.url,
        "project": record.project,
        "people": list(record.people),
        "tags": list(record.tags),
        "confidence": record.confidence,
        "needs_confirmation": record.needs_confirmation,
        "raw": record.raw,
    }


def _write_raw_bundle(path_value: Any, bundle: ActivityBundle) -> None:
    if not path_value:
        return
    path = Path(str(path_value)).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bundle_to_jsonable(bundle), ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    log(f"已写入原始采集数据: {path}")


def _notification_excerpt(summary: str, limit: int = 1400) -> str:
    """Feishu IM messages do not render Markdown headings (``##``) or horizontal
    rules (``---``); dumping the raw report as one space-joined line shows those
    markers literally and reads as "no formatting". Convert to IM-friendly rich
    text instead: headings become bold lines, dividers are dropped, bullets are
    normalised to ``•``, and line breaks are preserved so the chat message keeps
    a clear visual structure (bold section titles + bulleted lines)."""
    lines: list[str] = []
    for raw in summary.splitlines():
        stripped = raw.strip()
        if not stripped:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if re.fullmatch(r"(?:[-*_—]\s*){3,}", stripped):  # horizontal rule
            continue
        heading = re.match(r"^#{1,6}\s+(.*)$", stripped)
        if heading:
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(f"**{heading.group(1).strip()}**")
            continue
        bullet = re.match(r"^[-*]\s+(.*)$", stripped)
        if bullet:
            lines.append(f"• {bullet.group(1).strip()}")
            continue
        lines.append(stripped)
    text = "\n".join(lines).strip()
    if len(text) <= limit:
        return text
    truncated = text[:limit]
    cut = truncated.rfind("\n")
    if cut > limit * 0.6:
        truncated = truncated[:cut]
    return truncated.rstrip() + "\n\n……（完整内容见上方文档）"
