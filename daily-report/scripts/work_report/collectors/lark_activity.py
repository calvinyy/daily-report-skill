from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from work_report.models import DateWindow, SourceRecord


SHANGHAI_TZ = timezone(timedelta(hours=8))


def _safe_call(lark: Any, args: list[str], timeout: int = 45) -> dict[str, Any]:
    try:
        result = lark.call(args, timeout=timeout)
    except Exception:
        return {"ok": False}
    return result if isinstance(result, dict) else {"ok": False}


def _items_from_data(data: Any, key: str = "items") -> list[dict[str, Any]]:
    items = data if isinstance(data, list) else data.get(key, []) if isinstance(data, dict) else []
    return [item for item in items if isinstance(item, dict)]


def _drive_search_items(result: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(result.get("results"), list):
        return [item for item in result["results"] if isinstance(item, dict)]
    data = result.get("data", {})
    if not isinstance(data, dict):
        return []
    for key in ("results", "items"):
        if isinstance(data.get(key), list):
            return [item for item in data[key] if isinstance(item, dict)]
    return []


def _collect_safely(collector: Any, *args: Any) -> list[SourceRecord]:
    try:
        records = collector(*args)
    except Exception:
        return []
    return records if isinstance(records, list) else []


def parse_time(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(SHANGHAI_TZ).replace(tzinfo=None)


def collect_calendar(lark: Any, window: DateWindow, timezone_offset: str) -> list[SourceRecord]:
    _ = timezone_offset
    result = _safe_call(lark, ["calendar", "+agenda", "--start", window.start_iso, "--end", window.end_iso], timeout=45)
    events = _items_from_data(result.get("data", []) if result.get("ok") else [])
    records: list[SourceRecord] = []
    for event in events:
        title = str(event.get("summary") or event.get("title") or "未命名日程")
        attendees = event.get("attendees", [])
        if not isinstance(attendees, list):
            attendees = []
        records.append(
            SourceRecord(
                source="feishu",
                source_detail="calendar",
                title=title,
                content=str(event.get("description") or title),
                timestamp=parse_time(str(event.get("start_time") or event.get("start") or "")),
                people=tuple(str(person) for person in attendees[:8]),
                tags=("会议讨论",),
                raw=event,
            )
        )
    return records


def collect_minutes(lark: Any, window: DateWindow) -> list[SourceRecord]:
    result = _safe_call(
        lark,
        [
            "minutes",
            "+search",
            "--start",
            window.start.strftime("%Y-%m-%d"),
            "--end",
            window.end.strftime("%Y-%m-%d"),
            "--participant-ids",
            "me",
        ],
        timeout=45,
    )
    data = result.get("data", {}) if result.get("ok") else {}
    records: list[SourceRecord] = []
    for item in _items_from_data(data):
        title = str(item.get("title") or "未命名会议纪要")
        records.append(
            SourceRecord(
                source="feishu",
                source_detail="minutes",
                title=title,
                content=str(item.get("summary") or item.get("abstract") or title),
                url=str(item.get("url") or ""),
                tags=("会议讨论", "决策"),
                raw=item,
            )
        )
    return records


def collect_messages(lark: Any, window: DateWindow, timezone_offset: str) -> list[SourceRecord]:
    _ = timezone_offset
    result = _safe_call(
        lark,
        [
            "im",
            "+messages-search",
            "--start",
            window.start_iso,
            "--end",
            window.end_iso,
            "--page-all",
            "--exclude-sender-type",
            "bot",
        ],
        timeout=60,
    )
    data = result.get("data", {}) if result.get("ok") else {}
    messages = _items_from_data(data, key="messages")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for message in messages:
        chat = str(message.get("chat_name") or message.get("chat_id") or "未知会话")
        grouped.setdefault(chat, []).append(message)

    records: list[SourceRecord] = []
    for chat, items in grouped.items():
        snippets = []
        people = set()
        for item in items[:20]:
            text = str(item.get("text") or item.get("content") or "")
            sender = item.get("sender") or {}
            if isinstance(sender, dict) and sender.get("name"):
                people.add(str(sender.get("name")))
            if text:
                snippets.append(text[:120])
        records.append(
            SourceRecord(
                source="feishu",
                source_detail="im",
                title=chat,
                content=f"会话消息 {len(items)} 条；代表内容: {'；'.join(snippets)}",
                people=tuple(sorted(people)),
                tags=("项目沟通",),
                raw={"message_count": len(items)},
            )
        )
    return records


def collect_drive_docs(lark: Any, window: DateWindow) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    queries = [
        (
            "edited",
            [
                "drive",
                "+search",
                "--query",
                "",
                "--edited-since",
                window.start.strftime("%Y-%m-%d"),
                "--edited-until",
                window.end.strftime("%Y-%m-%d"),
                "--sort",
                "edit_time",
            ],
        ),
        (
            "commented",
            [
                "drive",
                "+search",
                "--query",
                "",
                "--commented-since",
                window.start.strftime("%Y-%m-%d"),
                "--commented-until",
                window.end.strftime("%Y-%m-%d"),
                "--sort",
                "edit_time",
            ],
        ),
    ]
    seen: set[str] = set()
    for detail, args in queries:
        result = _safe_call(lark, args, timeout=45)
        if not result.get("ok"):
            continue
        for item in _drive_search_items(result):
            url = str(item.get("url") or "")
            key = url or str(item.get("token") or item.get("title"))
            if key in seen:
                continue
            seen.add(key)
            records.append(
                SourceRecord(
                    source="feishu",
                    source_detail=f"drive-{detail}",
                    title=str(item.get("title") or "未命名文档"),
                    content=str(item.get("summary") or item.get("title") or ""),
                    url=url,
                    tags=("产品方案", "产出") if detail == "edited" else ("项目沟通",),
                    raw=item,
                )
            )
            records.extend(collect_unresolved_comments(lark, item))
    return records


def extract_doc_token(item: dict[str, Any]) -> str:
    token = str(item.get("token") or item.get("file_token") or "")
    if token:
        return token
    url = str(item.get("url") or "")
    path = urlparse(url).path.strip("/")
    parts = path.split("/")
    if len(parts) >= 2 and parts[0] in {"docx", "docs", "wiki"}:
        return parts[1]
    return ""


def infer_comment_file_type(item: dict[str, Any]) -> str:
    explicit_type = str(item.get("type") or item.get("file_type") or "").lower()
    if explicit_type in {"docx", "doc"}:
        return explicit_type
    url = str(item.get("url") or "")
    path = urlparse(url).path.strip("/")
    parts = path.split("/")
    if len(parts) >= 2 and parts[0] == "docs":
        return "doc"
    return "docx"


def collect_unresolved_comments(lark: Any, item: dict[str, Any]) -> list[SourceRecord]:
    doc_type = infer_comment_file_type(item)
    if doc_type not in {"docx", "doc"}:
        return []
    token = extract_doc_token(item)
    if not token:
        return []
    result = _safe_call(
        lark,
        [
            "drive",
            "file.comments",
            "list",
            "--params",
            f'{{"file_token":"{token}","file_type":"{doc_type}","is_solved":false}}',
        ],
        timeout=45,
    )
    comments = _items_from_data(result.get("data", {}) if result.get("ok") else {})
    if not comments:
        return []

    snippets: list[str] = []
    people: set[str] = set()
    for comment in comments[:10]:
        reply_list = comment.get("reply_list", {})
        replies = reply_list.get("replies", []) if isinstance(reply_list, dict) else []
        for reply in [entry for entry in replies[:3] if isinstance(entry, dict)]:
            content = str(reply.get("content") or reply.get("text") or "")
            if content:
                snippets.append(content[:120])
            if reply.get("user_id"):
                people.add(str(reply.get("user_id")))
    title = str(item.get("title") or "未命名文档")
    return [
        SourceRecord(
            source="feishu",
            source_detail="drive-comments",
            title=f"{title} - 未解决评论",
            content=f"未解决评论 {len(comments)} 条；代表内容: {'；'.join(snippets)}",
            url=str(item.get("url") or ""),
            people=tuple(sorted(people)),
            tags=("项目沟通", "待办"),
            raw={"comment_count": len(comments), "doc_token": token},
        )
    ]


def collect_tasks(lark: Any) -> list[SourceRecord]:
    result = _safe_call(lark, ["task", "+get-related-tasks", "--format", "json"], timeout=45)
    data = result.get("data", {}) if result.get("ok") else {}
    records: list[SourceRecord] = []
    for item in _items_from_data(data)[:50]:
        records.append(
            SourceRecord(
                source="feishu",
                source_detail="task",
                title=str(item.get("summary") or item.get("title") or "未命名待办"),
                content=str(item.get("description") or item.get("summary") or ""),
                url=str(item.get("url") or ""),
                tags=("待办",),
                raw=item,
            )
        )
    return records


def collect_lark_activity(lark: Any, window: DateWindow, config: dict[str, Any]) -> list[SourceRecord]:
    timezone_offset = str(config.get("timezone_offset") or "+08:00")
    records: list[SourceRecord] = []
    records.extend(_collect_safely(collect_calendar, lark, window, timezone_offset))
    records.extend(_collect_safely(collect_minutes, lark, window))
    records.extend(_collect_safely(collect_messages, lark, window, timezone_offset))
    records.extend(_collect_safely(collect_drive_docs, lark, window))
    records.extend(_collect_safely(collect_tasks, lark))
    return records
