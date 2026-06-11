import json
import sqlite3
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from work_report.collectors.browser_activity import collect_browser_domains
from work_report.collectors.local_ai import (
    collect_claude_history,
    collect_codex_from_db,
    collect_git_commits,
    collect_tool_traces_from_rollout,
)
from work_report.models import ReportKind, build_date_window


SHANGHAI = timezone(timedelta(hours=8))
CHROME_EPOCH = 11644473600000000


def local_ms(value: datetime) -> int:
    return int(value.replace(tzinfo=SHANGHAI).timestamp() * 1000)


def chrome_time(value: datetime) -> int:
    return int(value.replace(tzinfo=SHANGHAI).timestamp() * 1000000) + CHROME_EPOCH


def test_collect_codex_from_db_reads_threads(tmp_path: Path):
    db_path = tmp_path / "state.sqlite"
    rollout_path = tmp_path / "rollout.jsonl"
    rollout_path.write_text(
        json.dumps(
            {
                "type": "response_item",
                "payload": {"role": "assistant", "content": [{"type": "output_text", "text": "完成登录方案和测试"}]},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE threads (id TEXT, title TEXT, first_user_message TEXT, cwd TEXT, created_at_ms INTEGER, rollout_path TEXT)"
    )
    conn.execute(
        "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?)",
        ("t1", "登录改造", "请实现海外账号登录", "/tmp/project", local_ms(datetime(2026, 5, 14, 10, 0, 0)), str(rollout_path)),
    )
    conn.commit()
    conn.close()
    window = build_date_window(date(2026, 5, 14), ReportKind.DAILY)

    records = collect_codex_from_db(db_path, window)

    assert len(records) == 1
    assert records[0].source == "codex"
    assert records[0].project == "project"
    assert "完成登录方案和测试" in records[0].content


def test_collect_codex_from_db_includes_final_fractional_second(tmp_path: Path):
    db_path = tmp_path / "state.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE threads (id TEXT, title TEXT, first_user_message TEXT, cwd TEXT, created_at_ms INTEGER, rollout_path TEXT)"
    )
    conn.execute(
        "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?)",
        ("last", "收尾", "最后一秒内的任务", "/tmp/project", local_ms(datetime(2026, 5, 14, 23, 59, 59, 999000)), ""),
    )
    conn.execute(
        "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?)",
        ("next", "明天", "不应进入当天", "/tmp/project", local_ms(datetime(2026, 5, 15, 0, 0, 0)), ""),
    )
    conn.commit()
    conn.close()
    window = build_date_window(date(2026, 5, 14), ReportKind.DAILY)

    records = collect_codex_from_db(db_path, window)

    assert [record.raw["thread_id"] for record in records] == ["last"]


def test_collect_tool_traces_extracts_browser_and_computer_use():
    lines = [
        {"type": "tool_call", "payload": {"name": "browser.open", "arguments": {"url": "http://localhost:3000"}}},
        {"type": "tool_call", "payload": {"name": "computer-use.click", "arguments": {"target": "Save"}}},
    ]

    records = collect_tool_traces_from_rollout(lines, datetime(2026, 5, 14, 9, 0, 0), "demo")

    assert [record.source for record in records] == ["browser", "computer"]
    assert records[0].tags == ("AI协作", "资料调研")
    assert records[1].tags == ("AI协作", "技术实现")


def test_collect_tool_traces_handles_json_string_and_malformed_args():
    lines = [
        {"type": "tool_call", "payload": {"name": "browser.open", "arguments": '{"url": "http://localhost:3000"}'}},
        {"type": "tool_call", "payload": {"name": "computer-use.click", "arguments": "{bad json"}},
    ]

    records = collect_tool_traces_from_rollout(lines, datetime(2026, 5, 14, 9, 0, 0), "demo")

    assert [record.source for record in records] == ["browser", "computer"]
    assert records[0].title == "http://localhost:3000"
    assert records[1].title == "Computer Use 操作"


def test_collect_browser_domains_counts_visits_in_window_and_skips_noise(tmp_path: Path):
    db_path = tmp_path / "History"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT)")
    conn.execute("CREATE TABLE visits (id INTEGER PRIMARY KEY, url INTEGER, visit_time INTEGER)")
    in_window = chrome_time(datetime(2026, 5, 14, 12, 0, 0))
    out_of_window = chrome_time(datetime(2026, 5, 13, 12, 0, 0))
    conn.execute("INSERT INTO urls VALUES (?, ?, ?)", (1, "https://example.com/a", "竞品资料"))
    conn.execute("INSERT INTO urls VALUES (?, ?, ?)", (2, "https://google.com/search", "Google"))
    conn.execute("INSERT INTO visits VALUES (?, ?, ?)", (1, 1, in_window))
    conn.execute("INSERT INTO visits VALUES (?, ?, ?)", (2, 1, in_window + 1))
    conn.execute("INSERT INTO visits VALUES (?, ?, ?)", (3, 1, out_of_window))
    conn.execute("INSERT INTO visits VALUES (?, ?, ?)", (4, 2, in_window))
    conn.commit()
    conn.close()
    window = build_date_window(date(2026, 5, 14), ReportKind.DAILY)

    records = collect_browser_domains(db_path, window, {"google.com"})

    assert len(records) == 1
    assert records[0].source == "browser"
    assert records[0].title == "example.com"
    assert "访问 2 次" in records[0].content


def test_collect_browser_domains_uses_exact_or_subdomain_skip_matching(tmp_path: Path):
    db_path = tmp_path / "History"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT)")
    conn.execute("CREATE TABLE visits (id INTEGER PRIMARY KEY, url INTEGER, visit_time INTEGER)")
    visit_time = chrome_time(datetime(2026, 5, 14, 12, 0, 0))
    conn.execute("INSERT INTO urls VALUES (?, ?, ?)", (1, "https://google.com/search", "Google"))
    conn.execute("INSERT INTO urls VALUES (?, ?, ?)", (2, "https://docs.google.com/document", "Docs"))
    conn.execute("INSERT INTO urls VALUES (?, ?, ?)", (3, "https://notgoogle.com/page", "Useful"))
    conn.execute("INSERT INTO urls VALUES (?, ?, ?)", (4, "http://localhost:3000/dashboard", "Local App"))
    conn.execute("INSERT INTO urls VALUES (?, ?, ?)", (5, "https://docs.google.com:443/document", "Docs Port"))
    conn.execute("INSERT INTO visits VALUES (?, ?, ?)", (1, 1, visit_time))
    conn.execute("INSERT INTO visits VALUES (?, ?, ?)", (2, 2, visit_time))
    conn.execute("INSERT INTO visits VALUES (?, ?, ?)", (3, 3, visit_time))
    conn.execute("INSERT INTO visits VALUES (?, ?, ?)", (4, 4, visit_time))
    conn.execute("INSERT INTO visits VALUES (?, ?, ?)", (5, 5, visit_time))
    conn.commit()
    conn.close()
    window = build_date_window(date(2026, 5, 14), ReportKind.DAILY)

    records = collect_browser_domains(db_path, window, {"google.com", "localhost"})

    assert [record.title for record in records] == ["notgoogle.com"]


def test_collect_claude_history_skips_malformed_timestamps(tmp_path: Path):
    history_path = tmp_path / "history.jsonl"
    history_path.write_text(
        "\n".join(
            [
                json.dumps({"timestamp": None, "display": "空时间"}),
                json.dumps({"timestamp": "not-a-number", "display": "坏时间"}),
                json.dumps({"timestamp": local_ms(datetime(2026, 5, 14, 11, 0, 0)), "display": "有效会话"}),
            ]
        ),
        encoding="utf-8",
    )
    window = build_date_window(date(2026, 5, 14), ReportKind.DAILY)

    records = collect_claude_history(history_path, window)

    assert [record.title for record in records] == ["有效会话"]


def test_collect_git_commits_skips_subprocess_exceptions(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    window = build_date_window(date(2026, 5, 14), ReportKind.DAILY)

    def raise_timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="git", timeout=20)

    monkeypatch.setattr(subprocess, "run", raise_timeout)

    assert collect_git_commits(window, [repo]) == []
