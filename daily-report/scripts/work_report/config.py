from __future__ import annotations

import copy
import json
import os
import shutil
from argparse import Namespace
from pathlib import Path
from typing import Any

from work_report.models import ReportKind


APP_DIR = Path.home() / ".daily-report-skill"
DEFAULT_CONFIG_PATH = APP_DIR / "config.json"
LOG_PATH = APP_DIR / "report.log"

DEFAULT_SKIP_DOMAINS = {
    "google.com",
    "www.google.com",
    "accounts.google.com",
    "accounts.feishu.cn",
    "localhost",
    "127.0.0.1",
}

DEFAULT_CONFIG: dict[str, Any] = {
    "daily_folder_name": "日报记录",
    "weekly_folder_name": "周报记录",
    "folder_name": "周报记录",
    "legacy_folder_name": "周报记录",
    # Report layout. "per_day": one doc per day, overwritten each run (default).
    # "weekly_sections": the monolith's model — one weekly doc per ISO week with
    # a per-day "## YYYY年MM月DD日（周X）" section, str_replace-or-append in place.
    "report_layout": "per_day",
    "enabled_sources": ["codex", "claude", "accio", "feishu", "browser", "computer", "git"],
    "accio_paths": [
        "~/.accio/history.jsonl",
        "~/.accio/tasks.jsonl",
        "~/Library/Application Support/Accio/history.jsonl",
    ],
    "notify_user_id": "",
    "notify_as": "user",
    "send_notification": True,
    "lark_cli": "",
    "codex_cli": "",
    "codex_model": "gpt-5.4-mini",
    "claude_cli": "",
    # Pin the Claude fallback model. `claude -p` with no --model uses the global
    # default in ~/.claude/settings.json, which can be an unavailable model
    # (e.g. claude-fable-5 went "currently unavailable") — every call then exits
    # 1 and the report silently degrades to the non-AI fallback. Keep this on an
    # always-available, capable model. Empty string = use Claude's own default.
    "claude_model": "claude-sonnet-4-6",
    "ai_cli": "",
    "ai_enabled": True,
    "timezone_offset": "+08:00",
    # Where the guard writes daily_YYYY-MM-DD.done flags; --watchdog reads them to
    # detect overdue-missing reports and stores its alert throttle state here.
    "flag_dir": "~/.report_flags",
    "chrome_profile": "Default",
    "skip_domains": sorted(DEFAULT_SKIP_DOMAINS),
    "max_prompt_records": 180,
    "include_raw_bundle_path": "",
}


def load_config(path: Path) -> dict[str, Any]:
    config = copy.deepcopy(DEFAULT_CONFIG)
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SystemExit(f"配置文件读取失败: {path} ({exc})") from exc
        if not isinstance(loaded, dict):
            raise SystemExit(f"配置文件必须是 JSON object: {path}")
        config.update(loaded)
        if "folder_name" in loaded:
            if "daily_folder_name" not in loaded:
                config["daily_folder_name"] = loaded["folder_name"]
            if "weekly_folder_name" not in loaded:
                config["weekly_folder_name"] = loaded["folder_name"]
    return config


def save_default_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        print(f"配置文件已存在: {path}")
        return
    path.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已创建配置文件: {path}")


def merge_cli_overrides(config: dict[str, Any], args: Namespace) -> dict[str, Any]:
    merged = copy.deepcopy(config)
    if args.folder_name:
        if args.kind == ReportKind.WEEKLY:
            merged["weekly_folder_name"] = args.folder_name
        else:
            merged["daily_folder_name"] = args.folder_name
        merged["folder_name"] = args.folder_name
    if args.source:
        merged["enabled_sources"] = list(args.source)
    if args.raw_bundle_path:
        merged["include_raw_bundle_path"] = args.raw_bundle_path
    if args.no_notify:
        merged["send_notification"] = False
    if args.notify_user_id:
        merged["notify_user_id"] = args.notify_user_id
    if args.no_ai:
        merged["ai_enabled"] = False
    if args.lark_cli:
        merged["lark_cli"] = args.lark_cli
    if args.codex_cli:
        merged["codex_cli"] = args.codex_cli
    if args.codex_model is not None:
        merged["codex_model"] = args.codex_model
    if args.claude_cli:
        merged["claude_cli"] = args.claude_cli
    if args.claude_model is not None:
        merged["claude_model"] = args.claude_model
    if args.ai_cli:
        merged["ai_cli"] = args.ai_cli
        merged["claude_cli"] = args.ai_cli
    return merged


def find_binary(configured: str, name: str, extra_paths: list[str] | None = None) -> str:
    candidates = []
    if configured:
        candidates.append(configured)
    found = shutil.which(name)
    if found:
        candidates.append(found)
    candidates.extend(extra_paths or [])
    for candidate in candidates:
        if not candidate:
            continue
        expanded = os.path.expanduser(candidate)
        if os.path.isabs(expanded) and os.path.exists(expanded):
            return expanded
        resolved = shutil.which(expanded)
        if resolved:
            return resolved
    return ""
