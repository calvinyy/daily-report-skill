from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from work_report.collectors.accio import collect_accio_paths
from work_report.collectors.browser_activity import collect_browser_domains
from work_report.collectors.lark_activity import collect_lark_activity
from work_report.collectors.local_ai import collect_claude_history, collect_codex_from_db, collect_git_commits
from work_report.models import ActivityBundle, DateWindow, SourceRecord


Collector = Callable[[], list[SourceRecord]]


def collect_activity(window: DateWindow, config: dict[str, Any], lark: Any) -> ActivityBundle:
    enabled = set(_as_list(config.get("enabled_sources")))
    records: list[SourceRecord] = []
    collection_status: dict[str, str] = {}

    if "codex" in enabled:
        _collect_source("codex", lambda: collect_codex_from_db(_codex_db_path(config), window), records, collection_status)

    if "claude" in enabled:
        _collect_source("claude", lambda: collect_claude_history(_claude_history_path(config), window), records, collection_status)

    if "accio" in enabled:
        _collect_source("accio", lambda: collect_accio_paths(_accio_paths(config), window), records, collection_status)

    if "feishu" in enabled:
        if lark is None:
            collection_status["feishu"] = "skipped: lark unavailable"
        else:
            _collect_source("feishu", lambda: collect_lark_activity(lark, window, config), records, collection_status)

    if "browser" in enabled:
        _collect_source(
            "browser",
            lambda: _collect_browser_profiles(config, window),
            records,
            collection_status,
        )

    if "git" in enabled:
        _collect_source("git", lambda: collect_git_commits(window, _git_repo_paths(config, records)), records, collection_status)

    if "computer" in enabled:
        collection_status.setdefault("computer", "ok: collected from codex rollout traces")

    filtered_records = [record for record in records if record.source in enabled]
    return ActivityBundle(records=filtered_records, collection_status=collection_status)


def _collect_source(source: str, collector: Collector, records: list[SourceRecord], collection_status: dict[str, str]) -> None:
    try:
        collected = collector()
    except Exception as exc:
        collection_status[source] = f"failed: {type(exc).__name__}: {exc}"
        return
    if not isinstance(collected, list):
        collection_status[source] = f"failed: expected list, got {type(collected).__name__}"
        return
    records.extend(collected)
    source_count = sum(1 for record in collected if record.source == source)
    collection_status[source] = f"ok: {source_count} records"


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item)]
    return []


def _codex_db_path(config: dict[str, Any]) -> Path:
    return Path(str(config.get("codex_db_path") or Path.home() / ".codex" / "state_5.sqlite")).expanduser()


def _claude_history_path(config: dict[str, Any]) -> Path:
    return Path(str(config.get("claude_history_path") or Path.home() / ".claude" / "history.jsonl")).expanduser()


def _accio_paths(config: dict[str, Any]) -> list[str]:
    paths = _as_list(config.get("accio_paths"))
    if paths:
        return paths
    return [
        "~/.accio/history.jsonl",
        "~/.accio/tasks.jsonl",
        "~/Library/Application Support/Accio/history.jsonl",
    ]


def _collect_browser_profiles(config: dict[str, Any], window: DateWindow) -> list[SourceRecord]:
    skip_domains = set(_as_list(config.get("skip_domains")))
    records: list[SourceRecord] = []
    for history_path in _chrome_history_paths(config):
        records.extend(collect_browser_domains(history_path, window, skip_domains))
    return records


def _chrome_history_paths(config: dict[str, Any]) -> list[Path]:
    if config.get("chrome_history_path"):
        return [Path(str(config["chrome_history_path"])).expanduser()]
    profiles = _as_list(config.get("chrome_profiles")) or _as_list(config.get("chrome_profile")) or ["Default"]
    return [
        Path.home() / "Library" / "Application Support" / "Google" / "Chrome" / profile / "History"
        for profile in profiles
    ]


def _git_repo_paths(config: dict[str, Any], records: list[SourceRecord]) -> list[Path]:
    candidates: list[Path] = []
    for record in records:
        if record.source != "codex":
            continue
        cwd = record.raw.get("cwd")
        if cwd:
            candidates.append(Path(str(cwd)).expanduser())
    candidates.append(Path.cwd())
    candidates.extend(Path(path).expanduser() for path in _as_list(config.get("git_repo_paths")))
    return _dedupe_paths(candidates)


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped
