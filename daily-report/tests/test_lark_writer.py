from datetime import date
from types import SimpleNamespace

import pytest

from work_report import lark_client
from work_report.lark_client import run_json
from work_report.lark_writer import (
    append_change_record,
    folder_name_for_kind,
    get_or_create_doc,
    get_or_create_folder,
    replace_or_append_section,
    report_doc_url,
    weekly_doc_title,
    write_report,
)
from work_report.models import ReportKind, build_date_window


class FakeLark:
    def __init__(self):
        self.calls = []

    def call(self, args, timeout=45):
        self.calls.append(args)
        command = " ".join(args)
        if "drive files list" in command:
            return {"ok": True, "data": {"files": []}}
        if "drive +create-folder" in command:
            return {"ok": True, "data": {"folder_token": "fld_daily"}}
        if "docs +create" in command:
            return {"ok": True, "data": {"document": {"document_id": "doc_daily"}}}
        if "docs +update" in command:
            return {"ok": True, "data": {"revision_id": "rev_1"}}
        return {"ok": True}


class QueueLark:
    def __init__(self, responses):
        self.calls = []
        self.responses = list(responses)

    def call(self, args, timeout=45):
        self.calls.append(args)
        if not self.responses:
            raise AssertionError(f"unexpected call: {args}")
        return self.responses.pop(0)


def test_folder_name_for_kind_uses_daily_and_weekly_config():
    assert folder_name_for_kind({"daily_folder_name": "日报", "weekly_folder_name": "周报"}, ReportKind.DAILY) == "日报"
    assert folder_name_for_kind({"daily_folder_name": "日报", "weekly_folder_name": "周报"}, ReportKind.WEEKLY) == "周报"


def test_report_doc_url_uses_docx_host():
    assert report_doc_url("doc_daily") == "https://feishu.cn/docx/doc_daily"


def test_run_json_forces_error_shape_when_nonzero_exit_has_json_stdout(monkeypatch):
    def fake_run(command, capture_output, text, timeout, env=None):
        assert command == ["lark-cli", "auth", "status"]
        assert capture_output is True
        assert text is True
        assert timeout == 7
        return SimpleNamespace(returncode=2, stdout='{"ok": true, "data": {"hint": "expired"}}', stderr="token expired")

    monkeypatch.setattr(lark_client.subprocess, "run", fake_run)

    result = run_json(["lark-cli", "auth", "status"], timeout=7)

    assert result["ok"] is False
    assert result["_error"] == "token expired"
    assert result["_command"] == ["lark-cli", "auth", "status"]
    assert result["_returncode"] == 2
    assert result["data"] == {"hint": "expired"}


def test_run_json_wraps_non_dict_json_stdout(monkeypatch):
    def fake_run(command, capture_output, text, timeout, env=None):
        return SimpleNamespace(returncode=0, stdout='["ok-ish"]', stderr="")

    monkeypatch.setattr(lark_client.subprocess, "run", fake_run)

    result = run_json(["lark-cli", "version"])

    assert result == {
        "ok": False,
        "_error": "JSON output was not an object",
        "_command": ["lark-cli", "version"],
        "_returncode": 0,
        "_raw_json": ["ok-ish"],
    }


def test_failed_folder_list_raises_without_creating():
    lark = QueueLark([{"ok": False, "_error": "token expired"}])

    with pytest.raises(SystemExit, match="查询飞书文件夹失败"):
        get_or_create_folder(lark, "日报记录")

    assert len(lark.calls) == 1
    assert lark.calls[0][:3] == ["drive", "files", "list"]


def test_failed_doc_list_raises_without_creating():
    lark = QueueLark([{"ok": False, "_error": "folder access denied"}])

    with pytest.raises(SystemExit, match="查询飞书文档失败"):
        get_or_create_doc(lark, "fld_1", "2026年第20周日报 05/14")

    assert len(lark.calls) == 1
    assert lark.calls[0][:3] == ["drive", "files", "list"]


SECTION_MD = "## 2026年05月14日（周四）\n\n### 今日工作总结\n- 完成登录方案梳理\n\n---\n\n"


def test_replace_range_not_found_falls_back_to_append_and_succeeds():
    lark = QueueLark(
        [
            {"ok": False, "_error": "selection not found"},
            {"ok": True, "data": {"revision_id": "rev_append"}},
        ]
    )

    replace_or_append_section(lark, "doc_1", "2026年05月14日（周四）", SECTION_MD)

    assert len(lark.calls) == 2
    assert lark.calls[0][lark.calls[0].index("--command") + 1] == "str_replace"
    assert "--pattern" in lark.calls[0]
    assert lark.calls[0][lark.calls[0].index("--pattern") + 1] == "## 2026年05月14日（周四）...---"
    assert lark.calls[1][lark.calls[1].index("--command") + 1] == "append"


def test_str_replace_success_does_not_append():
    lark = QueueLark([{"ok": True, "data": {"result": "success"}}])

    replace_or_append_section(lark, "doc_1", "2026年05月14日（周四）", SECTION_MD)

    assert len(lark.calls) == 1
    assert lark.calls[0][lark.calls[0].index("--command") + 1] == "str_replace"


def test_str_replace_permission_failure_raises_without_append():
    lark = QueueLark([{"ok": False, "_error": "permission denied"}])

    with pytest.raises(SystemExit, match="更新飞书文档章节失败"):
        replace_or_append_section(lark, "doc_1", "标题", SECTION_MD)

    assert len(lark.calls) == 1
    assert lark.calls[0][lark.calls[0].index("--command") + 1] == "str_replace"


def test_str_replace_explicit_false_ok_with_revision_raises_without_append():
    lark = QueueLark([{"ok": False, "data": {"revision_id": "rev"}, "_error": "command exited 1"}])

    with pytest.raises(SystemExit, match="更新飞书文档章节失败"):
        replace_or_append_section(lark, "doc_1", "标题", SECTION_MD)

    assert len(lark.calls) == 1


def test_append_fallback_failure_raises():
    lark = QueueLark(
        [
            {"ok": False, "_error": "selection not found"},
            {"ok": False, "_error": "append failed"},
        ]
    )

    with pytest.raises(SystemExit, match="追加飞书文档章节失败"):
        replace_or_append_section(lark, "doc_1", "标题", SECTION_MD)

    assert len(lark.calls) == 2
    assert lark.calls[1][lark.calls[1].index("--command") + 1] == "append"


def test_change_record_appends_entry_with_command_append():
    lark = QueueLark([{"ok": True, "data": {"revision_id": "rev_change_record"}}])

    append_change_record(lark, "doc_1", "Riemann", "生成/更新 标题")

    assert len(lark.calls) == 1
    assert lark.calls[0][lark.calls[0].index("--command") + 1] == "append"
    content = lark.calls[0][lark.calls[0].index("--content") + 1]
    assert "Riemann" in content
    assert "生成/更新 标题" in content


def test_change_record_failure_raises():
    lark = QueueLark([{"ok": False, "_error": "permission denied"}])

    with pytest.raises(SystemExit, match="追加飞书改动记录失败"):
        append_change_record(lark, "doc_1", "Riemann", "生成/更新 标题")

    assert len(lark.calls) == 1


def test_existing_folder_and_doc_are_reused_without_create_calls():
    lark = QueueLark(
        [
            {"ok": True, "data": {"files": [{"name": "日报记录", "type": "folder", "token": "fld_existing"}]}},
            {"ok": True, "data": {"files": [{"name": "2026年第20周日报 05/14", "type": "docx", "token": "doc_existing"}]}},
        ]
    )

    folder_token = get_or_create_folder(lark, "日报记录")
    doc_token = get_or_create_doc(lark, folder_token, "2026年第20周日报 05/14")

    assert folder_token == "fld_existing"
    assert doc_token == "doc_existing"
    assert all(call[:2] != ["drive", "+create-folder"] for call in lark.calls)
    assert all(call[:2] != ["docs", "+create"] for call in lark.calls)


def test_existing_folder_accepts_lark_cli_code_zero_success_shape():
    lark = QueueLark(
        [
            {
                "code": 0,
                "msg": "success",
                "data": {"files": [{"name": "日报记录", "type": "folder", "token": "fld_existing"}]},
            }
        ]
    )

    assert get_or_create_folder(lark, "日报记录") == "fld_existing"
    assert all(call[:2] != ["drive", "+create-folder"] for call in lark.calls)


def test_write_report_per_day_creates_folder_doc_and_overwrites():
    lark = FakeLark()
    window = build_date_window(date(2026, 5, 14), ReportKind.DAILY)

    result = write_report(
        lark=lark,
        window=window,
        markdown="## 今日工作总结\n- 完成登录方案梳理",
        config={"daily_folder_name": "日报记录", "weekly_folder_name": "周报记录"},
        editor="Riemann",
    )

    assert result.doc_token == "doc_daily"
    assert result.url == "https://feishu.cn/docx/doc_daily"
    assert any(call[:2] == ["drive", "+create-folder"] for call in lark.calls)
    assert any(call[:2] == ["docs", "+create"] for call in lark.calls)
    overwrite = next(
        call for call in lark.calls if call[:2] == ["docs", "+update"] and "overwrite" in call
    )
    assert overwrite[overwrite.index("--command") + 1] == "overwrite"
    assert "--content" in overwrite


def test_write_report_per_day_strips_fallback_h1_inside_daily_section():
    lark = FakeLark()
    window = build_date_window(date(2026, 5, 14), ReportKind.DAILY)

    write_report(
        lark=lark,
        window=window,
        markdown="# 2026年05月14日（周四）\n\n## 今日工作总结\n- 完成登录方案梳理",
        config={"daily_folder_name": "日报记录", "weekly_folder_name": "周报记录"},
        editor="Riemann",
    )

    overwrite = next(
        call for call in lark.calls if call[:2] == ["docs", "+update"] and "overwrite" in call
    )
    markdown = overwrite[overwrite.index("--content") + 1]
    assert markdown.startswith("# 2026年第20周日报 05/14\n\n## 2026年05月14日（周四）\n\n## 今日工作总结")
    assert "# 2026年05月14日（周四）" not in markdown.splitlines()
    assert "## 改动记录" in markdown


def test_weekly_doc_title_spans_iso_week():
    assert weekly_doc_title(date(2026, 5, 14)) == "第20周 05/11-05/17"


def test_write_report_weekly_sections_writes_day_section_into_weekly_doc():
    # str_replace misses (first write of the day) -> falls back to append.
    lark = QueueLark(
        [
            {"ok": True, "data": {"files": [{"name": "周报记录", "type": "folder", "token": "fld_week"}]}},
            {"ok": True, "data": {"files": [{"name": "第20周 05/11-05/17", "type": "docx", "token": "doc_week"}]}},
            {"ok": True, "data": {"result": "failed"}},
            {"ok": True, "data": {"revision_id": "rev_append"}},
        ]
    )
    window = build_date_window(date(2026, 5, 14), ReportKind.DAILY)

    result = write_report(
        lark=lark,
        window=window,
        markdown="## 今日工作总结\n- 完成登录方案梳理",
        config={"weekly_folder_name": "周报记录", "report_layout": "weekly_sections"},
        editor="Riemann",
    )

    assert result.doc_token == "doc_week"
    str_replace = next(call for call in lark.calls if "str_replace" in call)
    assert str_replace[str_replace.index("--pattern") + 1] == "## 2026年05月14日（周四）...---"
    append = next(call for call in lark.calls if "append" in call)
    section = append[append.index("--content") + 1]
    # Day heading is the only H2; the summary's H2 was demoted to H3.
    assert section.startswith("## 2026年05月14日（周四）\n\n### 今日工作总结")
    assert section.rstrip().endswith("---")
    # No per-day doc was created in the daily folder.
    assert all(call[:2] != ["docs", "+create"] for call in lark.calls)
