from datetime import date, datetime

from work_report.collectors import lark_activity
from work_report.collectors.lark_activity import collect_drive_docs, collect_lark_activity, parse_time
from work_report.models import ReportKind, build_date_window


class FakeLark:
    def __init__(self):
        self.calls = []

    def call(self, args, timeout=45):
        self.calls.append(args)
        joined = " ".join(args)
        if "calendar +agenda" in joined:
            return {"ok": True, "data": [{"summary": "登录方案评审", "start_time": "2026-05-14T10:00:00+08:00"}]}
        if "minutes +search" in joined:
            return {"ok": True, "data": {"items": [{"title": "登录方案评审纪要", "url": "https://feishu.cn/minutes/min_1"}]}}
        if "im +messages-search" in joined:
            return {"ok": True, "data": {"messages": [{"chat_name": "项目群", "text": "确认本周完成登录灰度", "sender": {"name": "Alice"}}]}}
        if "drive +search" in joined:
            return {"ok": True, "results": [{"title": "海外账号登录方案", "url": "https://feishu.cn/docx/doc_1", "type": "docx", "token": "doc_1"}]}
        if "drive file.comments list" in joined:
            return {
                "ok": True,
                "data": {
                    "items": [
                        {
                            "comment_id": "c1",
                            "is_solved": False,
                            "reply_list": {"replies": [{"content": "需要补充灰度策略", "user_id": "Alice"}]},
                        }
                    ]
                },
            }
        if "task +get-related-tasks" in joined:
            return {"ok": True, "data": {"items": [{"summary": "补齐登录埋点", "url": "https://feishu.cn/task/t1"}]}}
        return {"ok": True}


def test_collect_lark_activity_includes_messages_docs_minutes_and_tasks():
    lark = FakeLark()
    window = build_date_window(date(2026, 5, 14), ReportKind.DAILY)

    records = collect_lark_activity(lark, window, {"timezone_offset": "+08:00"})

    titles = [record.title for record in records]
    assert "登录方案评审" in titles
    assert "登录方案评审纪要" in titles
    assert "项目群" in titles
    assert "海外账号登录方案" in titles
    assert "海外账号登录方案 - 未解决评论" in titles
    assert "补齐登录埋点" in titles
    assert any(record.source == "feishu" and "会议讨论" in record.tags for record in records)


def test_parse_time_converts_aware_timestamp_to_shanghai_naive():
    assert parse_time("2026-05-14T02:00:00Z") == datetime(2026, 5, 14, 10, 0, 0)
    assert parse_time("2026-05-14T10:00:00+08:00") == datetime(2026, 5, 14, 10, 0, 0)
    assert parse_time("2026-05-14T10:00:00") == datetime(2026, 5, 14, 10, 0, 0)


def test_collect_lark_activity_continues_after_collector_raises(monkeypatch):
    window = build_date_window(date(2026, 5, 14), ReportKind.DAILY)

    def raise_calendar(*_args):
        raise RuntimeError("calendar failed unexpectedly")

    monkeypatch.setattr(lark_activity, "collect_calendar", raise_calendar)

    records = collect_lark_activity(FakeLark(), window, {"timezone_offset": "+08:00"})

    assert "补齐登录埋点" in [record.title for record in records]


class DriveItemsLark:
    def __init__(self):
        self.calls = []

    def call(self, args, timeout=45):
        self.calls.append(args)
        joined = " ".join(args)
        if "drive +search" in joined and "--edited-since" in args:
            return {
                "ok": True,
                "data": {
                    "items": [
                        {
                            "title": "data.items 文档",
                            "url": "https://feishu.cn/docx/doc_items",
                            "type": "docx",
                            "token": "doc_items",
                        }
                    ]
                },
            }
        if "drive +search" in joined and "--commented-since" in args:
            return {"ok": True, "data": {"items": []}}
        if "drive file.comments list" in joined:
            return {"ok": True, "data": {"items": []}}
        return {"ok": True}


def test_collect_drive_docs_accepts_data_items_response():
    window = build_date_window(date(2026, 5, 14), ReportKind.DAILY)

    records = collect_drive_docs(DriveItemsLark(), window)

    assert [record.title for record in records] == ["data.items 文档"]


class LegacyDocsLark:
    def __init__(self):
        self.calls = []

    def call(self, args, timeout=45):
        self.calls.append(args)
        joined = " ".join(args)
        if "drive +search" in joined and "--edited-since" in args:
            return {"ok": True, "data": {"items": [{"title": "旧文档", "url": "https://feishu.cn/docs/doc_legacy"}]}}
        if "drive +search" in joined and "--commented-since" in args:
            return {"ok": True, "data": {"items": []}}
        if "drive file.comments list" in joined:
            return {
                "ok": True,
                "data": {
                    "items": [
                        {
                            "comment_id": "c1",
                            "is_solved": False,
                            "reply_list": {"replies": [{"content": "旧文档评论"}]},
                        }
                    ]
                },
            }
        return {"ok": True}


def test_legacy_docs_url_uses_doc_comment_file_type():
    lark = LegacyDocsLark()
    window = build_date_window(date(2026, 5, 14), ReportKind.DAILY)

    records = collect_drive_docs(lark, window)

    comment_call = next(call for call in lark.calls if call[:3] == ["drive", "file.comments", "list"])
    params = comment_call[comment_call.index("--params") + 1]
    assert '"file_type":"doc"' in params
    assert "旧文档 - 未解决评论" in [record.title for record in records]


class DuplicateDriveLark:
    def __init__(self):
        self.calls = []

    def call(self, args, timeout=45):
        self.calls.append(args)
        joined = " ".join(args)
        item = {"title": "重复文档", "url": "https://feishu.cn/docx/doc_dup", "type": "docx", "token": "doc_dup"}
        if "drive +search" in joined and "--edited-since" in args:
            return {"ok": True, "results": [item]}
        if "drive +search" in joined and "--commented-since" in args:
            return {"ok": True, "data": {"results": [item]}}
        if "drive file.comments list" in joined:
            return {"ok": True, "data": {"items": []}}
        return {"ok": True}


def test_collect_drive_docs_dedupes_edited_and_commented_results():
    window = build_date_window(date(2026, 5, 14), ReportKind.DAILY)

    records = collect_drive_docs(DuplicateDriveLark(), window)

    assert [record.title for record in records] == ["重复文档"]
