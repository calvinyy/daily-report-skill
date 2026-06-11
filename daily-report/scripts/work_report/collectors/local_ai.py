from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from work_report.models import DateWindow, SourceRecord


SHANGHAI_TZ = timezone(timedelta(hours=8))


def _local_datetime_to_epoch_ms(value: datetime) -> int:
    return int(value.replace(tzinfo=SHANGHAI_TZ).timestamp() * 1000)


def _epoch_ms_to_local_datetime(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=SHANGHAI_TZ).replace(tzinfo=None)


def _window_epoch_ms(window: DateWindow) -> tuple[int, int]:
    return (
        _local_datetime_to_epoch_ms(window.start),
        _local_datetime_to_epoch_ms(window.end + timedelta(seconds=1)),
    )


def _coerce_epoch_ms(value: Any) -> int | None:
    try:
        timestamp_ms = int(value)
    except (TypeError, ValueError):
        return None
    return timestamp_ms if timestamp_ms > 0 else None


def _parse_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not path.exists() or not path.is_file():
        return items
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, dict):
            items.append(item)
    return items


def read_last_assistant_summary(rollout_path: str) -> str:
    last = ""
    for item in read_jsonl(Path(rollout_path)):
        if item.get("type") != "response_item":
            continue
        payload = item.get("payload", {})
        if payload.get("role") != "assistant":
            continue
        for content in payload.get("content", []):
            if content.get("type") == "output_text":
                last = str(content.get("text") or "")
    return last[:1200]


def collect_tool_traces_from_rollout(items: list[dict[str, Any]], timestamp: datetime, project: str) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for item in items:
        payload = item.get("payload", {})
        name = str(payload.get("name") or item.get("name") or "")
        arguments = _parse_arguments(payload.get("arguments") or item.get("arguments") or {})
        if "browser" in name:
            records.append(
                SourceRecord(
                    source="browser",
                    source_detail=name,
                    title=str(arguments.get("url") or arguments.get("target") or "浏览器操作"),
                    content=json.dumps(arguments, ensure_ascii=False),
                    timestamp=timestamp,
                    project=project,
                    tags=("AI协作", "资料调研"),
                )
            )
        if "computer" in name:
            records.append(
                SourceRecord(
                    source="computer",
                    source_detail=name,
                    title=str(arguments.get("target") or arguments.get("app") or "Computer Use 操作"),
                    content=json.dumps(arguments, ensure_ascii=False),
                    timestamp=timestamp,
                    project=project,
                    tags=("AI协作", "技术实现"),
                )
            )
    return records


def collect_codex_from_db(db_path: Path, window: DateWindow) -> list[SourceRecord]:
    if not db_path.exists():
        return []
    day_start, day_end = _window_epoch_ms(window)
    records: list[SourceRecord] = []
    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.Error:
        return records
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, title, first_user_message, cwd, created_at_ms, rollout_path
            FROM threads
            WHERE created_at_ms >= ? AND created_at_ms < ?
            ORDER BY created_at_ms
            """,
            (day_start, day_end),
        )
        for thread_id, title, first_msg, cwd, created_at, rollout_path in cursor.fetchall():
            try:
                timestamp_ms = _coerce_epoch_ms(created_at)
                timestamp = _epoch_ms_to_local_datetime(timestamp_ms) if timestamp_ms else None
                project = Path(cwd).name if cwd else "unknown"
                summary = read_last_assistant_summary(str(rollout_path)) if rollout_path else ""
                display_title = str(title or first_msg or thread_id or "未命名 Codex 任务")[:160]
                content = summary or str(first_msg or "")
                records.append(
                    SourceRecord(
                        source="codex",
                        source_detail="state_5.sqlite",
                        title=display_title,
                        content=content[:1200],
                        timestamp=timestamp,
                        project=project,
                        tags=("AI协作", "技术实现"),
                        raw={"thread_id": thread_id, "rollout_path": rollout_path, "cwd": cwd},
                    )
                )
                if rollout_path:
                    records.extend(collect_tool_traces_from_rollout(read_jsonl(Path(rollout_path)), timestamp or window.start, project))
            except (TypeError, ValueError, OSError):
                continue
    except sqlite3.Error:
        return records
    finally:
        conn.close()
    return records


def collect_claude_history(history_path: Path, window: DateWindow) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    seen: set[str] = set()
    window_start, window_end = _window_epoch_ms(window)
    for entry in read_jsonl(history_path):
        timestamp_ms = _coerce_epoch_ms(entry.get("timestamp"))
        if not timestamp_ms:
            continue
        if not (window_start <= timestamp_ms < window_end):
            continue
        timestamp = _epoch_ms_to_local_datetime(timestamp_ms)
        session_id = str(entry.get("sessionId") or f"{timestamp_ms}:{entry.get('display')}")
        if session_id in seen:
            continue
        seen.add(session_id)
        project = Path(str(entry.get("project") or "")).name or "unknown"
        records.append(
            SourceRecord(
                source="claude",
                source_detail="history.jsonl",
                title=str(entry.get("display") or "Claude 会话")[:160],
                content=str(entry.get("display") or "")[:1200],
                timestamp=timestamp,
                project=project,
                tags=("AI协作", "产品方案"),
                raw=entry,
            )
        )
    return records


def collect_git_commits(window: DateWindow, repo_paths: list[Path]) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for repo in repo_paths:
        if not (repo / ".git").exists():
            continue
        try:
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "log",
                    "--since",
                    window.start.replace(tzinfo=SHANGHAI_TZ).isoformat(),
                    "--before",
                    (window.end + timedelta(seconds=1)).replace(tzinfo=SHANGHAI_TZ).isoformat(),
                    "--pretty=format:%H%x09%ad%x09%s",
                    "--date=iso-strict",
                ],
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
            continue
        if result.returncode != 0:
            continue
        for line in result.stdout.splitlines():
            parts = line.split("\t", 2)
            if len(parts) != 3:
                continue
            commit_hash, raw_time, subject = parts
            try:
                parsed_time = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
                if parsed_time.tzinfo is not None:
                    parsed_time = parsed_time.astimezone(SHANGHAI_TZ).replace(tzinfo=None)
            except ValueError:
                continue
            records.append(
                SourceRecord(
                    source="git",
                    source_detail="commit",
                    title=subject,
                    content=f"提交 {commit_hash[:10]}: {subject}",
                    timestamp=parsed_time,
                    project=repo.name,
                    tags=("技术实现", "产出"),
                    raw={"hash": commit_hash},
                )
            )
    return records
