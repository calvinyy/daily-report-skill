"""After a daily report is written, fill the shared team tracking sheet:
one row per person, two columns per date (今日完成 / 明日计划). We locate the
person's row and the date's column pair, condense the report into at most N
one-sentence points each, and write them.

Config (config.json → "team_sheet"):
    {
      "enabled": true,
      "spreadsheet_token": "Kx3Ds5mJ4hVlPQtqAGAcCwI0nSf",
      "sheet_id": "09e850",
      "name": "Calvin",
      "name_col": "A",
      "date_header_row": 2,
      "max_points": 5
    }

Kept deliberately small and defensive: any failure here must NOT fail the
daily report (the caller wraps this and only logs).
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date, timedelta
from typing import Any, Callable

DONE_PRIMARY = "今日工作总结"
DONE_SECONDARY = "主要项目进展"
PLAN_SECTIONS = ("后续事项", "下周计划")

# strip these from every extracted point
_SOURCE_RE = re.compile(r"[（(]\s*来源[:：][^）)]*[）)]")
_BULLET_RE = re.compile(r"^[\-\*•·]+\s*")
_ENUM_RE = re.compile(r"^(?:[0-9]{1,2}[.、)]|[①-⑳]|[（(][0-9]{1,2}[）)])\s*")
_SENT_SPLIT_RE = re.compile(r"[；;。\n]|[①-⑳]|(?<=[^0-9])[0-9]{1,2}[、.)]")


def col_letter(n: int) -> str:
    """1-based column index → A1 letters (1→A, 27→AA)."""
    out = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def _clean(text: str) -> str:
    text = _SOURCE_RE.sub("", text)
    text = text.replace("**", "").replace("`", "").strip()
    text = _BULLET_RE.sub("", text)
    text = _ENUM_RE.sub("", text)
    text = text.strip(" 　-：:。.，,、;；")
    return text.strip()


def _sections(markdown: str) -> dict[str, list[str]]:
    """Map each '## heading' → its body lines (until the next '## ')."""
    out: dict[str, list[str]] = {}
    current: str | None = None
    for raw in markdown.splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            current = line[3:].strip()
            out[current] = []
        elif current is not None:
            out[current].append(line)
    return out


def _sentences(text: str) -> list[str]:
    out: list[str] = []
    for frag in _SENT_SPLIT_RE.split(text):
        if not frag:
            continue
        if frag.strip().endswith(("：", ":")):  # lead-in like "今天主要推进了三个方向："
            continue
        p = _clean(frag)
        if len(p) >= 4:
            out.append(p)
    return out


def extract_points(markdown: str, limit: int) -> tuple[list[str], list[str]]:
    """Return (done_points, plan_points), each ≤ limit one-sentence items."""
    sections = _sections(markdown)

    done: list[str] = []
    # Prefer the summary paragraph (describes what was done), split into points.
    for line in sections.get(DONE_PRIMARY, []):
        if line.strip():
            done.extend(_sentences(line))
    # Backfill from project sub-headings if the summary was thin.
    if len(done) < limit:
        for line in sections.get(DONE_SECONDARY, []):
            if line.startswith("### "):
                name = _clean(line[4:])
                if name and name not in done:
                    done.append(name)

    plan: list[str] = []
    for sec in PLAN_SECTIONS:
        for line in sections.get(sec, []):
            s = line.strip()
            if s.startswith(("-", "*", "•")):
                p = _clean(s)
                if p and p not in plan:
                    plan.append(p)

    return _dedup(done)[:limit], _dedup(plan)[:limit]


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if it and it not in seen:
            seen.add(it)
            out.append(it)
    return out


def format_cell(points: list[str]) -> str:
    return "\n".join(f"• {p}" for p in points)


def _read_csv(lark: Any, token: str, sheet_id: str, a1_range: str) -> list[list[str]]:
    result = lark.call(
        ["sheets", "+csv-get", "--spreadsheet-token", token, "--sheet-id", sheet_id,
         "--range", a1_range, "--include-row-prefix=false"],
        timeout=30,
    )
    text = (result.get("data", {}) or {}).get("annotated_csv", "") if isinstance(result, dict) else ""
    return list(csv.reader(io.StringIO(text)))


def resolve_target(
    lark: Any, token: str, sheet_id: str, name: str, name_col: str,
    header_row: int, target_date: date, max_col: int = 89, max_row: int = 200,
) -> tuple[int, str, str] | None:
    """Find (person_row, 今日完成 col letter, 明日计划 col letter). None if not found."""
    header = _read_csv(lark, token, sheet_id, f"A{header_row}:{col_letter(max_col)}{header_row}")
    if not header:
        return None
    cells = header[0]
    md = f"{target_date.month}/{target_date.day}"
    date_re = re.compile(r"(?<!\d)" + re.escape(md) + r"(?!\d)")
    done_col = next((i + 1 for i, c in enumerate(cells) if date_re.search(c or "")), None)
    if not done_col:
        return None

    names = _read_csv(lark, token, sheet_id, f"{name_col}1:{name_col}{max_row}")
    row = next((i + 1 for i, r in enumerate(names) if r and r[0].strip() == name), None)
    if not row:
        return None
    return row, col_letter(done_col), col_letter(done_col + 1)


def _read_cell(lark: Any, token: str, sheet_id: str, a1: str) -> str:
    rows = _read_csv(lark, token, sheet_id, f"{a1}:{a1}")
    return (rows[0][0].strip() if rows and rows[0] else "")


def _may_overwrite(existing: str) -> bool:
    """Only write when the cell is empty or holds a value we wrote ourselves
    (bullet-prefixed). Never clobber the user's own manual entries."""
    return existing == "" or existing.lstrip().startswith("•")


def _write_cell(lark: Any, token: str, sheet_id: str, a1: str, text: str, overwrite: bool = False) -> bool:
    if not overwrite:
        existing = _read_cell(lark, token, sheet_id, a1)
        if not _may_overwrite(existing):
            return True  # leave the user's manual entry untouched
    import json as _json
    result = lark.call(
        ["sheets", "+cells-set", "--spreadsheet-token", token, "--sheet-id", sheet_id,
         "--range", a1, "--cells", _json.dumps([[{"value": text}]], ensure_ascii=False)],
        timeout=30,
    )
    return result.get("ok") is not False


def fill_team_sheet(
    lark: Any, config: dict[str, Any], target_date: date, summary_md: str,
    log: Callable[[str], None] = lambda _m: None,
) -> bool:
    cfg = config.get("team_sheet") or {}
    if not cfg.get("enabled"):
        return True
    token = cfg.get("spreadsheet_token")
    sheet_id = cfg.get("sheet_id")
    name = cfg.get("name")
    if not (token and sheet_id and name):
        log("团队表格配置不完整，跳过")
        return True

    limit = int(cfg.get("max_points") or 3)
    # Log a report for day D into day D's column by default (offset 0);
    # configurable via date_offset_days.
    col_date = target_date + timedelta(days=int(cfg.get("date_offset_days", 0)))
    done, plan = extract_points(summary_md, limit)
    target = resolve_target(
        lark, token, sheet_id, name, str(cfg.get("name_col") or "A"),
        int(cfg.get("date_header_row") or 2), col_date,
    )
    if not target:
        log(f"团队表格未找到 {name} 或日期 {col_date} 对应列，跳过")
        return False
    row, done_col, plan_col = target
    overwrite = bool(cfg.get("overwrite"))
    ok1 = _write_cell(lark, token, sheet_id, f"{done_col}{row}", format_cell(done), overwrite)
    ok2 = _write_cell(lark, token, sheet_id, f"{plan_col}{row}", format_cell(plan), overwrite)
    if ok1 and ok2:
        log(f"团队表格已填写 {name} {col_date}（{done_col}{row}/{plan_col}{row}）")
        return True
    log("团队表格写入失败")
    return False
