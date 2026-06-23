from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape
from typing import Any

from work_report.models import DateWindow, ReportKind


@dataclass(frozen=True)
class WriteResult:
    doc_token: str
    url: str


def report_doc_url(doc_token: str) -> str:
    return f"https://feishu.cn/docx/{doc_token}"


def folder_name_for_kind(config: dict[str, Any], kind: ReportKind) -> str:
    key = "weekly_folder_name" if kind == ReportKind.WEEKLY else "daily_folder_name"
    return str(config.get(key) or config.get("legacy_folder_name") or "周报记录")


def _call_succeeded(result: dict[str, Any]) -> bool:
    if result.get("ok") is False:
        return False
    if result.get("code") in (0, "0"):
        return True
    data = result.get("data", {})
    revision_id = data.get("revision_id") if isinstance(data, dict) else None
    return bool(result.get("ok") or result.get("revision") or revision_id)


def _error_message(result: dict[str, Any]) -> str:
    error = result.get("error", {})
    if isinstance(error, dict):
        error_message = error.get("message") or error.get("msg") or ""
    else:
        error_message = str(error or "")
    return " ".join(str(part) for part in (result.get("_error"), error_message, result.get("message")) if part)


def _is_selection_missing_error(result: dict[str, Any]) -> bool:
    message = _error_message(result).lower()
    if not message:
        return False
    non_fallback_terms = ("permission", "forbidden", "unauthorized", "auth", "token", "scope", "权限", "未授权")
    if any(term in message for term in non_fallback_terms):
        return False
    missing_terms = (
        "selection not found",
        "title not found",
        "range not found",
        "not found",
        "not exist",
        "not_exists",
        "cannot find",
        "no matched",
        "unmatched",
        "未找到",
        "找不到",
        "不存在",
    )
    return any(term in message for term in missing_terms)


def _require_success(result: dict[str, Any], message: str) -> None:
    if _call_succeeded(result):
        return
    raise SystemExit(f"{message}: {_error_message(result) or result}")


def get_or_create_folder(lark: Any, folder_name: str) -> str:
    result = lark.call(["drive", "files", "list", "--params", '{"folder_token":""}', "--page-all"])
    _require_success(result, "查询飞书文件夹失败")
    for item in result.get("data", {}).get("files", []):
        if item.get("name") == folder_name and item.get("type") == "folder":
            return str(item["token"])
    created = lark.call(["drive", "+create-folder", "--name", folder_name], timeout=45)
    token = created.get("data", {}).get("folder_token") or created.get("data", {}).get("token") or created.get("folder_token")
    if not token:
        raise SystemExit(f"创建飞书文件夹失败: {created.get('_error') or created}")
    return str(token)


def get_or_create_doc(lark: Any, folder_token: str, title: str) -> str:
    result = lark.call(
        ["drive", "files", "list", "--params", json.dumps({"folder_token": folder_token}, ensure_ascii=False), "--page-all"]
    )
    _require_success(result, "查询飞书文档失败")
    for item in result.get("data", {}).get("files", []):
        if item.get("name") == title:
            return str(item["token"])
    created = lark.call(
        [
            "docs",
            "+create",
            "--api-version",
            "v2",
            "--parent-token",
            folder_token,
            "--doc-format",
            "xml",
            "--content",
            f"<title>{escape(title)}</title><p></p>",
        ],
        timeout=60,
    )
    token = (
        created.get("data", {}).get("document", {}).get("document_id")
        or created.get("data", {}).get("document_id")
        or created.get("data", {}).get("doc_id")
        or created.get("data", {}).get("token")
        or created.get("doc_id")
    )
    if not token:
        raise SystemExit(f"创建飞书文档失败: {created.get('_error') or created}")
    return str(token)


def replace_or_append_section(lark: Any, doc_token: str, heading: str, section_md: str) -> None:
    """Write one day's "## {heading}" block into a weekly doc (weekly_sections
    layout). Idempotent re-run: str_replace the existing block in place, matched
    by the `## {heading}...---` ellipsis pattern (atomic only because the day
    heading is the block's sole `##` — inner sections are demoted to `###`).
    First write of the day: the pattern misses, so fall back to append. A real
    error (permission/auth) raises without appending a duplicate."""
    replaced = lark.call(
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
            f"## {heading}...---",
            "--content",
            section_md.rstrip(),
        ],
        timeout=60,
    )
    data = replaced.get("data", {}) if isinstance(replaced, dict) else {}
    if _call_succeeded(replaced) and data.get("result") in ("success", "partial_success"):
        return
    if not _call_succeeded(replaced) and not _is_selection_missing_error(replaced):
        raise SystemExit(f"更新飞书文档章节失败: {_error_message(replaced) or replaced}")
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
    _require_success(appended, "追加飞书文档章节失败")


def overwrite_report_doc(lark: Any, doc_token: str, markdown: str) -> None:
    result = lark.call(
        [
            "docs",
            "+update",
            "--api-version",
            "v2",
            "--doc",
            doc_token,
            "--command",
            "overwrite",
            "--doc-format",
            "markdown",
            "--content",
            markdown,
        ],
        timeout=60,
    )
    _require_success(result, "覆盖飞书日报/周报文档失败")


def append_change_record(lark: Any, doc_token: str, editor: str, change_summary: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"- {timestamp} | {editor} | {change_summary}\n"
    result = lark.call(
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
            entry,
        ],
        timeout=60,
    )
    _require_success(result, "追加飞书改动记录失败")


def write_report(lark: Any, window: DateWindow, markdown: str, config: dict[str, Any], editor: str) -> WriteResult:
    if str(config.get("report_layout") or "per_day") == "weekly_sections":
        return _write_weekly_sections(lark, window, markdown, config)
    return _write_per_day(lark, window, markdown, config, editor)


def _write_per_day(lark: Any, window: DateWindow, markdown: str, config: dict[str, Any], editor: str) -> WriteResult:
    folder_token = get_or_create_folder(lark, folder_name_for_kind(config, window.kind))
    doc_token = get_or_create_doc(lark, folder_token, window.doc_title)
    document_md = _report_document_markdown(window, markdown)
    overwrite_report_doc(lark, doc_token, document_md)
    append_change_record(lark, doc_token, editor, f"生成/更新 {window.section_heading}")
    return WriteResult(doc_token=doc_token, url=report_doc_url(doc_token))


def _write_weekly_sections(lark: Any, window: DateWindow, markdown: str, config: dict[str, Any]) -> WriteResult:
    folder_name = str(config.get("weekly_folder_name") or config.get("folder_name") or "周报记录")
    folder_token = get_or_create_folder(lark, folder_name)
    doc_token = get_or_create_weekly_doc(lark, folder_token, weekly_doc_title(window.target_date))
    section_md = _day_section_markdown(window, markdown)
    replace_or_append_section(lark, doc_token, window.section_heading, section_md)
    return WriteResult(doc_token=doc_token, url=report_doc_url(doc_token))


def weekly_doc_title(target_date: Any) -> str:
    monday = target_date - timedelta(days=target_date.weekday())
    sunday = monday + timedelta(days=6)
    week = target_date.isocalendar().week
    return f"第{week}周 {monday.strftime('%m/%d')}-{sunday.strftime('%m/%d')}"


def get_or_create_weekly_doc(lark: Any, folder_token: str, title: str) -> str:
    result = lark.call(
        ["drive", "files", "list", "--params", json.dumps({"folder_token": folder_token}, ensure_ascii=False), "--page-all"]
    )
    _require_success(result, "查询飞书文档失败")
    for item in result.get("data", {}).get("files", []):
        if item.get("name") == title:
            return str(item["token"])
    # Feishu names an untitled docx from its first H1, so create with a markdown
    # "# {title}" body (the monolith's proven naming; --title is silently dropped).
    created = lark.call(
        [
            "docs",
            "+create",
            "--api-version",
            "v2",
            "--parent-token",
            folder_token,
            "--content",
            f"# {title}\n",
            "--doc-format",
            "markdown",
        ],
        timeout=60,
    )
    token = (
        created.get("data", {}).get("document", {}).get("document_id")
        or created.get("data", {}).get("document_id")
        or created.get("data", {}).get("doc_id")
        or created.get("data", {}).get("token")
        or created.get("doc_id")
    )
    if not token:
        raise SystemExit(f"创建飞书周报文档失败: {created.get('_error') or created}")
    return str(token)


def _day_section_markdown(window: DateWindow, markdown: str) -> str:
    # Demote the summary's H2 sections to H3 so the day heading is the block's
    # only "##" — required for the str_replace "## {heading}...---" pattern to
    # match the whole day block atomically.
    body = re.sub(r"(?m)^## ", "### ", _strip_leading_h1(markdown.strip()))
    return f"## {window.section_heading}\n\n{body}\n\n---\n\n"


def _report_document_markdown(window: DateWindow, markdown: str) -> str:
    return f"# {window.doc_title}\n\n## {window.section_heading}\n\n{_strip_leading_h1(markdown.strip())}\n\n---\n\n## 改动记录\n\n"


def _strip_leading_h1(markdown: str) -> str:
    lines = markdown.splitlines()
    if lines and lines[0].startswith("# ") and not lines[0].startswith("## "):
        lines = lines[1:]
        while lines and not lines[0].strip():
            lines = lines[1:]
    return "\n".join(lines).strip()
