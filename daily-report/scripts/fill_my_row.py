#!/usr/bin/env python3
"""Fill the caller's own row in the team daily-tracking sheet (软件部每日工作追踪
= the team sheet) with up to 3 SHORT items (≤20 chars each) for today's
今日完成, and optionally 明日计划.

The distillation to "top-3, one short phrase each" is the caller's job (an AI
agent does it well); this script only does the reliable, fiddly part: resolve
the person's row and today's date column (which shifts as 回顾/下周todo columns
get inserted) and write without ever clobbering a manual entry.

    python3 fill_my_row.py --done "喷头校准开发" "仓库迁移" "打印模拟软件交付"
    python3 fill_my_row.py --done "..." --plan "..." --date 2026-08-19 --name Calvin
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from work_report.config import DEFAULT_CONFIG_PATH, find_binary, load_config  # noqa: E402
from work_report.lark_client import LarkClient  # noqa: E402
from work_report.team_sheet import _write_cell, format_cell, resolve_target  # noqa: E402

CHAR_CAP = 20
MAX_ITEMS = 3


def shorten(items: list[str], limit: int = MAX_ITEMS, cap: int = CHAR_CAP) -> list[str]:
    out: list[str] = []
    for raw in items:
        s = raw.strip().lstrip("•-*· ").strip()
        if not s:
            continue
        out.append(s[:cap])  # hard cap; caller should already keep it short
        if len(out) >= limit:
            break
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="Fill my own row in the team daily-tracking sheet.")
    p.add_argument("--done", nargs="*", default=[], help="today's done items (≤3, each ≤20 chars)")
    p.add_argument("--plan", nargs="*", default=[], help="optional tomorrow-plan items (≤3)")
    p.add_argument("--date", help="ISO date; default today (system time)")
    p.add_argument("--name", default="Calvin", help="row name in column A (default Calvin)")
    args = p.parse_args()

    cfg = load_config(DEFAULT_CONFIG_PATH)
    ts = cfg.get("team_sheet") or {}
    token, sid = ts.get("spreadsheet_token"), ts.get("sheet_id")
    if not (token and sid):
        print("team_sheet 配置缺失")
        return 1
    lark = LarkClient(find_binary(str(cfg.get("lark_cli") or ""), "lark-cli", ["/opt/homebrew/bin/lark-cli"]))
    d = date.fromisoformat(args.date) if args.date else datetime.now().date()

    target = resolve_target(lark, token, sid, args.name, str(ts.get("name_col") or "A"),
                            int(ts.get("date_header_row") or 2), d)
    if not target:
        print(f"未找到 {args.name} 或日期 {d} 对应列")
        return 1
    row, done_col, plan_col = target
    done, plan = shorten(args.done), shorten(args.plan)
    if done:
        _write_cell(lark, token, sid, f"{done_col}{row}", format_cell(done))
    if plan:
        _write_cell(lark, token, sid, f"{plan_col}{row}", format_cell(plan))
    print(f"已填 {args.name} {d}：今日完成 {done_col}{row}={done}" + (f"；明日计划 {plan_col}{row}={plan}" if plan else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
