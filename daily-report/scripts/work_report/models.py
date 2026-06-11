from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Any


WEEKDAY_CN = ["一", "二", "三", "四", "五", "六", "日"]
LOCAL_TIMEZONE_OFFSET = "+08:00"


class ReportKind(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"


@dataclass(frozen=True)
class DateWindow:
    """Naive Asia/Shanghai local datetimes with explicit +08:00 ISO rendering."""

    kind: ReportKind
    target_date: date
    start: datetime
    end: datetime
    label: str
    section_heading: str
    doc_title: str

    @property
    def start_iso(self) -> str:
        return self.start.strftime(f"%Y-%m-%dT%H:%M:%S{LOCAL_TIMEZONE_OFFSET}")

    @property
    def end_iso(self) -> str:
        return self.end.strftime(f"%Y-%m-%dT%H:%M:%S{LOCAL_TIMEZONE_OFFSET}")


@dataclass(frozen=True)
class SourceRecord:
    source: str
    title: str
    content: str
    source_detail: str = ""
    timestamp: datetime | None = None
    url: str = ""
    project: str = ""
    people: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    confidence: float = 1.0
    needs_confirmation: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    def source_label(self) -> str:
        labels = {
            "codex": "Codex",
            "claude": "Claude",
            "accio": "Accio",
            "feishu": "飞书",
            "browser": "Browser Use",
            "computer": "Computer Use",
            "git": "Git",
        }
        base = labels.get(self.source, self.source)
        return f"{base} / {self.source_detail}" if self.source_detail else base

    def to_prompt_line(self) -> str:
        stamp = self.timestamp.strftime("%Y-%m-%d %H:%M") if self.timestamp else "时间未知"
        project = f" | 项目: {self.project}" if self.project else ""
        people = f" | 相关人: {', '.join(self.people)}" if self.people else ""
        tags = f" | 类型: {', '.join(self.tags)}" if self.tags else ""
        confirm = " | 需确认" if self.needs_confirmation else ""
        link = f" | 链接: {self.url}" if self.url else ""
        return (
            f"- {stamp} | 来源: {self.source_label()}{project}{people}{tags}{confirm}{link}\n"
            f"  标题: {self.title}\n"
            f"  内容: {self.content}"
        )


@dataclass(frozen=True)
class ActivityBundle:
    records: list[SourceRecord]
    collection_status: dict[str, str] = field(default_factory=dict)

    def by_source(self) -> dict[str, list[SourceRecord]]:
        grouped: dict[str, list[SourceRecord]] = defaultdict(list)
        for record in self.records:
            grouped[record.source].append(record)
        return dict(grouped)

    def sorted_records(self) -> list[SourceRecord]:
        return sorted(
            self.records,
            key=lambda record: (
                record.timestamp or datetime.min,
                record.source,
                record.title,
            ),
        )


def build_date_window(target_date: date, kind: ReportKind) -> DateWindow:
    if kind == ReportKind.DAILY:
        start = datetime.combine(target_date, time.min)
        end = datetime.combine(target_date, time(23, 59, 59))
        weekday = WEEKDAY_CN[target_date.weekday()]
        label = target_date.strftime("%Y年%m月%d日")
        iso_calendar = target_date.isocalendar()
        return DateWindow(
            kind=kind,
            target_date=target_date,
            start=start,
            end=end,
            label=label,
            section_heading=f"{label}（周{weekday}）",
            doc_title=f"{iso_calendar.year}年第{iso_calendar.week:02d}周日报 {target_date.strftime('%m/%d')}",
        )

    monday = target_date - timedelta(days=target_date.weekday())
    sunday = monday + timedelta(days=6)
    start = datetime.combine(monday, time.min)
    end = datetime.combine(sunday, time(23, 59, 59))
    iso_calendar = target_date.isocalendar()
    return DateWindow(
        kind=kind,
        target_date=target_date,
        start=start,
        end=end,
        label=f"{iso_calendar.year}年第{iso_calendar.week:02d}周",
        section_heading=f"{iso_calendar.year}年第{iso_calendar.week:02d}周工作周报",
        doc_title=f"{iso_calendar.year}年第{iso_calendar.week:02d}周周报 {monday.strftime('%m/%d')}-{sunday.strftime('%m/%d')}",
    )
