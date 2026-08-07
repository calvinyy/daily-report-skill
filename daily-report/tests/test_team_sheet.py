from datetime import date

from work_report.team_sheet import (
    col_letter,
    extract_points,
    fill_team_sheet,
    format_cell,
    resolve_target,
)


def test_col_letter():
    assert col_letter(1) == "A"
    assert col_letter(10) == "J"
    assert col_letter(11) == "K"
    assert col_letter(26) == "Z"
    assert col_letter(27) == "AA"


SAMPLE_MD = """# 2026年08月07日（周五）

## 今日工作总结
今天主要推进了三个方向：①完成登录改造；②修复液位显示缺陷；③评审测试接口需求。（来源: x）

## 主要项目进展
### RiemannDesktopStudio
- 关键动作：重构 engine 分支
### 墨路板SDK
- 关键动作：补齐错误码

## 后续事项
- 继续推进喷头校准（来源: y）
- 确认负压达标状态位
"""


def test_extract_points_splits_summary_and_plan():
    done, plan = extract_points(SAMPLE_MD, limit=5)
    assert "完成登录改造" in done
    assert "修复液位显示缺陷" in done
    assert "评审测试接口需求" in done
    # lead-in clause must be dropped, source annotation stripped
    assert not any("今天主要推进" in p for p in done)
    assert len(done) <= 5
    assert "继续推进喷头校准" in plan
    assert "确认负压达标状态位" in plan
    assert all("来源" not in p for p in plan)


def test_extract_points_respects_limit():
    md = "## 今日工作总结\n" + "；".join(f"完成任务{i}" for i in range(10)) + "。\n"
    done, _ = extract_points(md, limit=5)
    assert len(done) == 5


def test_format_cell():
    assert format_cell(["甲", "乙"]) == "• 甲\n• 乙"
    assert format_cell([]) == ""


class FakeLark:
    def __init__(self, header_csv, name_csv, cells=None):
        self.header_csv = header_csv
        self.name_csv = name_csv
        self.cells = cells or {}  # existing cell content by A1, for read-before-write
        self.writes = []

    def call(self, args, timeout=30):
        if "+csv-get" in args:
            rng = args[args.index("--range") + 1]
            if rng.startswith("A2:"):
                return {"data": {"annotated_csv": self.header_csv}}
            if rng.startswith("A1:A"):
                return {"data": {"annotated_csv": self.name_csv}}
            a1 = rng.split(":")[0]  # single-cell read like "J4:J4"
            return {"data": {"annotated_csv": self.cells.get(a1, "")}}
        if "+cells-set" in args:
            self.writes.append((args[args.index("--range") + 1], args[args.index("--cells") + 1]))
            return {"ok": True}
        return {"ok": True}


HEADER = ",8/3 周一,,8/4 周二,,8/5 周三,,8/6 周四,,8/7 周五,,总结与问题\n"
NAMES = "姓名\n\n\nCalvin\nCian\nDaniel\n"


def test_resolve_target_finds_row_and_date_columns():
    lark = FakeLark(HEADER, NAMES)
    target = resolve_target(lark, "tok", "sid", "Calvin", "A", 2, date(2026, 8, 7))
    assert target == (4, "J", "K")


def test_resolve_target_date_not_double_matched():
    # 8/1 must not match inside 8/10 etc.; here only 8/7 exists so 8/17 shouldn't false-hit
    lark = FakeLark(HEADER, NAMES)
    assert resolve_target(lark, "tok", "sid", "Calvin", "A", 2, date(2026, 8, 1)) is None


def test_fill_team_sheet_writes_two_cells():
    lark = FakeLark(HEADER, NAMES)
    config = {"team_sheet": {"enabled": True, "spreadsheet_token": "tok", "sheet_id": "sid", "name": "Calvin"}}
    ok = fill_team_sheet(lark, config, date(2026, 8, 7), SAMPLE_MD)
    assert ok is True
    ranges = [w[0] for w in lark.writes]
    assert ranges == ["J4", "K4"]


def test_fill_team_sheet_disabled_is_noop():
    lark = FakeLark(HEADER, NAMES)
    assert fill_team_sheet(lark, {"team_sheet": {"enabled": False}}, date(2026, 8, 7), SAMPLE_MD) is True
    assert lark.writes == []


def test_fill_team_sheet_does_not_clobber_manual_entries():
    # J4 already has a manual "1. ..." entry; K4 empty → only K4 gets written.
    lark = FakeLark(HEADER, NAMES, cells={"J4": "1. 手动写的内容"})
    config = {"team_sheet": {"enabled": True, "spreadsheet_token": "tok", "sheet_id": "sid", "name": "Calvin"}}
    fill_team_sheet(lark, config, date(2026, 8, 7), SAMPLE_MD)
    ranges = [w[0] for w in lark.writes]
    assert "J4" not in ranges  # manual entry preserved
    assert "K4" in ranges


def test_fill_team_sheet_updates_own_bullet_entry():
    # A cell we previously wrote ("• ...") may be refreshed.
    lark = FakeLark(HEADER, NAMES, cells={"J4": "• 旧内容", "K4": "• 旧计划"})
    config = {"team_sheet": {"enabled": True, "spreadsheet_token": "tok", "sheet_id": "sid", "name": "Calvin"}}
    fill_team_sheet(lark, config, date(2026, 8, 7), SAMPLE_MD)
    ranges = [w[0] for w in lark.writes]
    assert "J4" in ranges and "K4" in ranges
