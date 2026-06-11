import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from work_report import collector_registry
from work_report.collector_registry import collect_activity
from work_report.collectors.accio import collect_accio_paths
from work_report.models import ReportKind, SourceRecord, build_date_window


SHANGHAI = timezone(timedelta(hours=8))


def local_ms(value: datetime) -> int:
    return int(value.replace(tzinfo=SHANGHAI).timestamp() * 1000)


def test_collect_accio_jsonl_marks_records_with_accio_source(tmp_path: Path):
    accio_path = tmp_path / "history.jsonl"
    accio_path.write_text(
        json.dumps(
            {
                "title": "海外账号资料调研",
                "content": "整理了登录和风控竞品资料",
                "timestamp": "2026-05-14T10:30:00+08:00",
                "project": "daily-report",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    window = build_date_window(date(2026, 5, 14), ReportKind.DAILY)

    records = collect_accio_paths([str(accio_path)], window)

    assert len(records) == 1
    assert records[0].source == "accio"
    assert records[0].source_detail == "local-jsonl"
    assert records[0].title == "海外账号资料调研"
    assert records[0].content == "整理了登录和风控竞品资料"
    assert records[0].timestamp == datetime(2026, 5, 14, 10, 30, 0)
    assert records[0].project == "daily-report"
    assert records[0].raw["path"] == str(accio_path)


def test_collect_accio_missing_paths_returns_confirmation_record(tmp_path: Path):
    window = build_date_window(date(2026, 5, 14), ReportKind.DAILY)

    records = collect_accio_paths([str(tmp_path / "missing.jsonl")], window)

    assert len(records) == 1
    assert records[0].source == "accio"
    assert records[0].needs_confirmation is True
    assert "未找到" in records[0].title


def test_collect_accio_existing_and_missing_paths_returns_record_and_confirmation(tmp_path: Path):
    accio_path = tmp_path / "history.jsonl"
    missing_path = tmp_path / "missing.jsonl"
    accio_path.write_text(
        json.dumps(
            {
                "title": "日报透明度检查",
                "content": "确认混合路径会保留缺失提示",
                "timestamp": "2026-05-14T14:00:00+08:00",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    window = build_date_window(date(2026, 5, 14), ReportKind.DAILY)

    records = collect_accio_paths([str(accio_path), str(missing_path)], window)

    assert len(records) == 2
    assert [record.needs_confirmation for record in records] == [False, True]
    assert records[0].title == "日报透明度检查"
    assert str(missing_path) in records[1].content


def test_registry_can_run_without_lark_for_local_sources(tmp_path: Path):
    codex_db = tmp_path / "state.sqlite"
    conn = sqlite3.connect(codex_db)
    conn.execute(
        "CREATE TABLE threads (id TEXT, title TEXT, first_user_message TEXT, cwd TEXT, created_at_ms INTEGER, rollout_path TEXT)"
    )
    conn.execute(
        "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?)",
        ("t1", "实现本地采集", "请连接本地采集器", "/tmp/daily-report", local_ms(datetime(2026, 5, 14, 9, 0, 0)), ""),
    )
    conn.commit()
    conn.close()
    accio_path = tmp_path / "accio.jsonl"
    accio_path.write_text(
        json.dumps(
            {"query": "日报采集方案", "answer": "确认 Accio JSONL 可以进入日报", "created_at": local_ms(datetime(2026, 5, 14, 11, 0, 0))},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    window = build_date_window(date(2026, 5, 14), ReportKind.DAILY)

    bundle = collect_activity(
        window,
        {
            "enabled_sources": ["codex", "accio", "feishu", "computer"],
            "codex_db_path": str(codex_db),
            "accio_paths": [str(accio_path)],
        },
        lark=None,
    )

    assert [record.source for record in bundle.sorted_records()] == ["codex", "accio"]
    assert bundle.collection_status["codex"] == "ok: 1 records"
    assert bundle.collection_status["accio"] == "ok: 1 records"
    assert bundle.collection_status["feishu"] == "skipped: lark unavailable"
    assert bundle.collection_status["computer"] == "ok: collected from codex rollout traces"


def test_registry_git_paths_include_codex_cwd_and_current_cwd(tmp_path: Path, monkeypatch):
    codex_cwd = tmp_path / "codex-project"
    current_cwd = tmp_path / "current-project"
    codex_cwd.mkdir()
    current_cwd.mkdir()
    captured: dict[str, list[Path]] = {}
    window = build_date_window(date(2026, 5, 14), ReportKind.DAILY)

    def fake_collect_codex_from_db(_db_path, _window):
        return [
            SourceRecord(
                source="codex",
                title="实现采集",
                content="连接 Git 采集路径",
                raw={"cwd": str(codex_cwd)},
            )
        ]

    def fake_collect_git_commits(_window, repo_paths):
        captured["repo_paths"] = repo_paths
        return []

    monkeypatch.chdir(current_cwd)
    monkeypatch.setattr(collector_registry, "collect_codex_from_db", fake_collect_codex_from_db)
    monkeypatch.setattr(collector_registry, "collect_git_commits", fake_collect_git_commits)

    collect_activity(window, {"enabled_sources": ["codex", "git"]}, lark=None)

    assert captured["repo_paths"] == [codex_cwd, current_cwd]


def test_registry_browser_iterates_configured_chrome_profiles(tmp_path: Path, monkeypatch):
    captured: list[Path] = []
    window = build_date_window(date(2026, 5, 14), ReportKind.DAILY)

    def fake_collect_browser_domains(history_path, _window, _skip_domains):
        captured.append(history_path)
        return []

    monkeypatch.setattr(collector_registry, "collect_browser_domains", fake_collect_browser_domains)
    monkeypatch.setattr(collector_registry.Path, "home", lambda: tmp_path)

    collect_activity(
        window,
        {"enabled_sources": ["browser"], "chrome_profiles": ["Default", "Profile 1"]},
        lark=None,
    )

    assert captured == [
        tmp_path / "Library" / "Application Support" / "Google" / "Chrome" / "Default" / "History",
        tmp_path / "Library" / "Application Support" / "Google" / "Chrome" / "Profile 1" / "History",
    ]


def test_registry_browser_honors_legacy_chrome_profile(tmp_path: Path, monkeypatch):
    captured: list[Path] = []
    window = build_date_window(date(2026, 5, 14), ReportKind.DAILY)

    def fake_collect_browser_domains(history_path, _window, _skip_domains):
        captured.append(history_path)
        return []

    monkeypatch.setattr(collector_registry, "collect_browser_domains", fake_collect_browser_domains)
    monkeypatch.setattr(collector_registry.Path, "home", lambda: tmp_path)

    collect_activity(window, {"enabled_sources": ["browser"], "chrome_profile": "Profile 1"}, lark=None)

    assert captured == [
        tmp_path / "Library" / "Application Support" / "Google" / "Chrome" / "Profile 1" / "History",
    ]
