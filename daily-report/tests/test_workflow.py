from datetime import date, datetime

from work_report import workflow
from work_report.lark_writer import WriteResult
from work_report.models import ActivityBundle, ReportKind, SourceRecord
from work_report.workflow import dry_run_markdown, run_install_check, run_workflow, send_notification


DAILY_HEADINGS = [
    "今日工作总结",
    "主要项目进展",
    "AI 工具协作",
    "沟通 & 会议",
    "后续事项",
]


class FakeLark:
    def __init__(self):
        self.calls = []

    def call(self, args, timeout=45):
        self.calls.append((args, timeout))
        return {"ok": True}


def sample_bundle() -> ActivityBundle:
    return ActivityBundle(
        records=[
            SourceRecord(
                source="codex",
                source_detail="state.sqlite",
                title="完成工作流编排",
                content="串联采集、总结和 dry-run 输出。",
                timestamp=datetime(2026, 5, 14, 10, 0, 0),
                tags=("开发",),
            )
        ],
        collection_status={"codex": "ok: 1 records"},
    )


def second_level_headings(markdown: str) -> list[str]:
    return [line.removeprefix("## ") for line in markdown.splitlines() if line.startswith("## ")]


def test_dry_run_markdown_contains_heading_and_summary():
    summary = "\n".join(
        [
            "## 今日工作总结",
            "- 完成 Task 8 编排。",
            "",
            "## 主要项目进展",
            "- daily-report: 串联采集、总结和 dry-run 输出。（来源: Codex / state.sqlite）",
            "",
            "## AI 工具协作",
            "- 工具: Codex / state.sqlite；问题: 完成工作流编排；产出: 串联采集、总结和 dry-run 输出。（来源: Codex / state.sqlite）",
            "",
            "## 沟通 & 会议",
            "- 暂无会议或沟通结论。",
            "",
            "## 后续事项",
            "- SENTINEL-END（来源: 测试）",
        ]
    )

    markdown = dry_run_markdown(
        target_date=date(2026, 5, 14),
        kind=ReportKind.DAILY,
        summary=summary,
        bundle=sample_bundle(),
    )

    assert "# 2026年05月14日（周四）" in markdown
    assert second_level_headings(markdown) == DAILY_HEADINGS
    assert "完成 Task 8 编排" in markdown
    assert "## 来源与采集状态" not in markdown
    assert "- 采集状态: codex: ok: 1 records（来源: 采集状态）" not in markdown
    assert markdown.endswith("- SENTINEL-END（来源: 测试）\n")


def test_dry_run_markdown_does_not_duplicate_existing_h1():
    summary = "# 自定义标题\n\n## 今日工作总结\n- SENTINEL-END"

    markdown = dry_run_markdown(
        target_date=date(2026, 5, 14),
        kind=ReportKind.DAILY,
        summary=summary,
        bundle=sample_bundle(),
    )

    assert markdown == summary + "\n"


def test_install_check_fails_without_lark_cli(capsys):
    code = run_install_check(config={}, lark_cli="", codex_cli="/bin/codex", claude_cli="")

    captured = capsys.readouterr()
    assert code == 1
    assert "lark-cli: missing" in captured.out


def test_send_notification_uses_markdown_message_command():
    lark = FakeLark()

    send_notification(
        lark=lark,
        doc_token="doc-token",
        target_date=date(2026, 5, 14),
        summary="## 今日工作总结\n- 完成通知修正。",
        notify_user_id="ou_test",
        notify_as="user",
    )

    assert len(lark.calls) == 1
    command, timeout = lark.calls[0]
    assert command[:2] == ["im", "+messages-send"]
    assert "--user-id" in command
    assert command[command.index("--user-id") + 1] == "ou_test"
    assert "--as" in command
    assert command[command.index("--as") + 1] == "user"
    assert "--markdown" in command
    markdown = command[command.index("--markdown") + 1]
    assert "工作报告已生成：2026-05-14" in markdown
    assert "https://feishu.cn/docx/doc-token" in markdown
    assert "完成通知修正" in markdown
    assert timeout == 30


def test_send_notification_skips_without_notify_user_id():
    lark = FakeLark()

    sent = send_notification(
        lark=lark,
        doc_token="doc-token",
        target_date=date(2026, 5, 14),
        summary="summary",
        notify_user_id="",
        notify_as="user",
    )

    assert lark.calls == []
    assert sent is True


class FailingNotifyLark:
    """OK for everything except the im +messages-send DM, which fails."""

    def __init__(self):
        self.calls = []

    def call(self, args, timeout=45):
        self.calls.append(args)
        if args[:2] == ["im", "+messages-send"]:
            return {"ok": False, "_error": "message send failed"}
        return {"ok": True}

    def current_user(self):
        return {"name": "Riemann", "open_id": "ou_test"}


def test_send_notification_returns_false_on_delivery_failure():
    lark = FailingNotifyLark()

    sent = send_notification(
        lark=lark,
        doc_token="doc-token",
        target_date=date(2026, 5, 14),
        summary="## 今日工作总结\n- x",
        notify_user_id="ou_test",
        notify_as="user",
    )

    assert sent is False


def test_run_workflow_fails_when_notification_fails(monkeypatch):
    lark = FailingNotifyLark()
    monkeypatch.setattr(workflow, "LarkClient", lambda binary: lark)
    monkeypatch.setattr(workflow, "collect_activity", lambda window, config, client: sample_bundle())
    monkeypatch.setattr(workflow, "summarize", lambda *args, **kwargs: "## 今日工作总结\n- 完成")
    monkeypatch.setattr(
        workflow,
        "write_report",
        lambda *args, **kwargs: WriteResult(doc_token="doc", url="https://feishu.cn/docx/doc"),
    )

    code = run_workflow(
        target_date=date(2026, 5, 14),
        kind=ReportKind.DAILY,
        config={"send_notification": True, "notify_user_id": "ou_test"},
        binaries={"lark": "/bin/lark-cli"},
        dry_run=False,
    )

    assert code == 1
    assert any(call[:2] == ["im", "+messages-send"] for call in lark.calls)
