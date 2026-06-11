from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from work_report.models import DateWindow, SourceRecord


SHANGHAI_TZ = timezone(timedelta(hours=8))


def parse_accio_time(raw: Any) -> datetime | None:
    if raw in (None, ""):
        return None
    if isinstance(raw, (int, float)):
        return _epoch_to_local_datetime(float(raw))
    if isinstance(raw, str):
        value = raw.strip()
        if not value:
            return None
        try:
            return _epoch_to_local_datetime(float(value))
        except ValueError:
            pass
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed
        return parsed.astimezone(SHANGHAI_TZ).replace(tzinfo=None)
    return None


def record_from_accio_entry(entry: dict[str, Any], path: str | Path) -> SourceRecord:
    timestamp = parse_accio_time(
        entry.get("timestamp")
        or entry.get("created_at")
        or entry.get("createdAt")
        or entry.get("updated_at")
        or entry.get("time")
        or entry.get("date")
    )
    title = _first_text(entry, ("title", "query", "prompt", "task", "name")) or "Accio 记录"
    content = _first_text(entry, ("content", "summary", "answer", "result", "response", "text", "description")) or title
    project = _first_text(entry, ("project", "workspace", "repo"))
    if not project and entry.get("cwd"):
        project = Path(str(entry.get("cwd"))).name
    return SourceRecord(
        source="accio",
        source_detail="local-jsonl",
        title=title[:160],
        content=content[:1200],
        timestamp=timestamp,
        project=project or "",
        tags=("AI协作", "资料调研"),
        needs_confirmation=timestamp is None,
        raw={**entry, "path": str(path)},
    )


def collect_accio_paths(raw_paths: list[str] | tuple[str, ...], window: DateWindow) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    checked_paths = [Path(raw_path).expanduser() for raw_path in raw_paths if str(raw_path).strip()]
    existing_paths = [path for path in checked_paths if path.exists() and path.is_file()]
    missing_paths = [path for path in checked_paths if path not in existing_paths]

    for path in existing_paths:
        for entry in _read_jsonl(path):
            record = record_from_accio_entry(entry, path)
            if record.timestamp and not _in_window(record.timestamp, window):
                continue
            records.append(record)

    if not existing_paths:
        display_paths = ", ".join(str(path) for path in checked_paths) or "未配置 Accio 路径"
        return [
            SourceRecord(
                source="accio",
                source_detail="local-jsonl",
                title="未找到 Accio 本地记录",
                content=f"已检查 Accio JSONL 路径: {display_paths}",
                needs_confirmation=True,
                tags=("AI协作",),
                raw={"paths": [str(path) for path in checked_paths]},
            )
        ]
    for path in missing_paths:
        records.append(_missing_path_record(path))
    return records


def _epoch_to_local_datetime(value: float) -> datetime | None:
    if value <= 0:
        return None
    if value > 1_000_000_000_000_000:
        value = value / 1_000_000
    elif value > 10_000_000_000:
        value = value / 1000
    try:
        return datetime.fromtimestamp(value, tz=SHANGHAI_TZ).replace(tzinfo=None)
    except (OSError, OverflowError, ValueError):
        return None


def _first_text(entry: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = entry.get(key)
        if value in (None, ""):
            continue
        if isinstance(value, str):
            return value.strip()
        return json.dumps(value, ensure_ascii=False)
    return ""


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return items
    for line in lines:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            items.append(item)
    return items


def _missing_path_record(path: Path) -> SourceRecord:
    return SourceRecord(
        source="accio",
        source_detail="local-jsonl",
        title="未找到 Accio 本地记录",
        content=f"未找到 Accio JSONL 文件: {path}",
        needs_confirmation=True,
        tags=("AI协作",),
        raw={"path": str(path)},
    )


def _in_window(timestamp: datetime, window: DateWindow) -> bool:
    return window.start <= timestamp < window.end + timedelta(seconds=1)
