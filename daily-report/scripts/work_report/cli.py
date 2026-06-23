from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path
from typing import Sequence

from work_report.config import DEFAULT_CONFIG_PATH, find_binary, load_config, merge_cli_overrides, save_default_config
from work_report.models import ReportKind


def report_kind(raw: str) -> ReportKind:
    try:
        return ReportKind(raw)
    except ValueError as exc:
        choices = ", ".join(kind.value for kind in ReportKind)
        raise argparse.ArgumentTypeError(f"invalid report kind: {raw!r} (choose from {choices})") from exc


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a Feishu/Lark work report.")
    parser.add_argument("positional_date", nargs="?", help="Report date, YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--kind", type=report_kind, default=ReportKind.DAILY, help="Report kind: daily or weekly.")
    parser.add_argument("--date", dest="date_value", help="Report date, YYYY-MM-DD. Overrides positional date.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Config JSON path.")
    parser.add_argument("--init-config", action="store_true", help="Create a default config file and exit.")
    parser.add_argument("--install-check", action="store_true", help="Check dependencies and Feishu auth.")
    parser.add_argument(
        "--watchdog",
        action="store_true",
        help="Run lightweight health checks (token expiry + missed-report alerts) and exit.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Collect data and print markdown without writing Feishu.")
    parser.add_argument("--no-ai", action="store_true", help="Do not call an AI CLI; use fallback formatting.")
    parser.add_argument("--no-notify", action="store_true", help="Do not send Feishu notification.")
    parser.add_argument("--folder-name", help="Feishu folder name. Defaults to config value.")
    parser.add_argument(
        "--source",
        action="append",
        choices=["codex", "claude", "accio", "feishu", "browser", "computer", "git"],
        help="Enable one source. Repeat to enable multiple sources.",
    )
    parser.add_argument("--raw-bundle-path", help="Write normalized source bundle JSON to this path.")
    parser.add_argument("--notify-user-id", help="Feishu open_id to notify. Defaults to current user.")
    parser.add_argument("--lark-cli", help="Path to lark-cli.")
    parser.add_argument("--codex-cli", help="Path to codex CLI.")
    parser.add_argument("--codex-model", help="Codex model for summarization. Empty config uses Codex default.")
    parser.add_argument("--claude-cli", help="Path to Claude CLI fallback.")
    parser.add_argument("--claude-model", help="Claude model for summarization fallback. Empty config uses Claude default.")
    parser.add_argument("--ai-cli", help="Legacy alias for --claude-cli.")
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_arg_parser().parse_args(argv)


def parse_target_date(raw: str | None) -> date:
    if not raw:
        return date.today()
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError as exc:
        raise SystemExit(f"日期格式错误: {raw}，请使用 YYYY-MM-DD") from exc


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    config_path = Path(args.config).expanduser()

    if args.init_config:
        save_default_config(config_path)
        return 0

    config = merge_cli_overrides(load_config(config_path), args)
    lark_cli = find_binary(str(config.get("lark_cli") or ""), "lark-cli", ["/opt/homebrew/bin/lark-cli"])
    codex_cli = find_binary(str(config.get("codex_cli") or ""), "codex", ["/opt/homebrew/bin/codex"])
    claude_cli = find_binary(
        str(config.get("claude_cli") or config.get("ai_cli") or ""),
        "claude",
        [str(Path.home() / ".local" / "bin" / "claude")],
    )

    from work_report.workflow import run_install_check, run_workflow

    if args.install_check:
        return run_install_check(config, lark_cli, codex_cli, claude_cli)

    if args.watchdog:
        from work_report.watchdog import run_watchdog

        return run_watchdog(config, binaries={"lark": lark_cli})

    target_date = parse_target_date(args.date_value or args.positional_date)
    return run_workflow(
        target_date=target_date,
        kind=args.kind,
        config=config,
        binaries={"lark": lark_cli, "codex": codex_cli, "claude": claude_cli},
        dry_run=args.dry_run,
    )
