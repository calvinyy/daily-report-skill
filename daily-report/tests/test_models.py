from datetime import date, datetime

from work_report.models import ActivityBundle, ReportKind, SourceRecord, build_date_window


def test_daily_window_uses_single_calendar_day():
    window = build_date_window(date(2026, 5, 14), ReportKind.DAILY)

    assert window.label == "2026年05月14日"
    assert window.start == datetime(2026, 5, 14, 0, 0, 0)
    assert window.end == datetime(2026, 5, 14, 23, 59, 59)
    assert window.section_heading == "2026年05月14日（周四）"
    assert window.doc_title == "2026年第20周日报 05/14"
    assert window.start_iso == "2026-05-14T00:00:00+08:00"
    assert window.end_iso == "2026-05-14T23:59:59+08:00"


def test_weekly_window_uses_monday_to_sunday():
    window = build_date_window(date(2026, 5, 14), ReportKind.WEEKLY)

    assert window.label == "2026年第20周"
    assert window.start == datetime(2026, 5, 11, 0, 0, 0)
    assert window.end == datetime(2026, 5, 17, 23, 59, 59)
    assert window.section_heading == "2026年第20周工作周报"
    assert window.doc_title == "2026年第20周周报 05/11-05/17"
    assert window.start_iso == "2026-05-11T00:00:00+08:00"
    assert window.end_iso == "2026-05-17T23:59:59+08:00"


def test_weekly_window_uses_iso_week_year_at_year_boundary():
    window = build_date_window(date(2021, 1, 1), ReportKind.WEEKLY)

    assert window.label == "2020年第53周"
    assert window.start == datetime(2020, 12, 28, 0, 0, 0)
    assert window.end == datetime(2021, 1, 3, 23, 59, 59)
    assert window.section_heading == "2020年第53周工作周报"
    assert window.doc_title == "2020年第53周周报 12/28-01/03"


def test_source_record_prompt_line_marks_uncertainty_and_source():
    record = SourceRecord(
        source="accio",
        source_detail="local-jsonl",
        title="搜索竞品资料",
        content="检索了海外账户体系的资料",
        timestamp=datetime(2026, 5, 14, 10, 30, 0),
        tags=("资料调研",),
        needs_confirmation=True,
    )

    line = record.to_prompt_line()

    assert "来源: Accio / local-jsonl" in line
    assert "需确认" in line
    assert "资料调研" in line
    assert "搜索竞品资料" in line


def test_activity_bundle_groups_records_by_source():
    bundle = ActivityBundle(
        records=[
            SourceRecord(source="codex", title="实现登录", content="修改登录流程"),
            SourceRecord(source="feishu", title="评审会", content="形成上线决策"),
        ],
        collection_status={"codex": "ok", "feishu": "ok"},
    )

    grouped = bundle.by_source()

    assert [record.title for record in grouped["codex"]] == ["实现登录"]
    assert [record.title for record in grouped["feishu"]] == ["评审会"]


def test_activity_bundle_sorts_records_by_timestamp_source_and_title():
    bundle = ActivityBundle(
        records=[
            SourceRecord(
                source="feishu",
                title="晚会",
                content="完成决策",
                timestamp=datetime(2026, 5, 14, 17, 0, 0),
            ),
            SourceRecord(source="codex", title="无时间", content="补充记录"),
            SourceRecord(
                source="codex",
                title="上午开发",
                content="实现模型",
                timestamp=datetime(2026, 5, 14, 9, 0, 0),
            ),
            SourceRecord(
                source="accio",
                title="上午调研",
                content="检索资料",
                timestamp=datetime(2026, 5, 14, 9, 0, 0),
            ),
        ],
    )

    sorted_titles = [record.title for record in bundle.sorted_records()]

    assert sorted_titles == ["无时间", "上午调研", "上午开发", "晚会"]
