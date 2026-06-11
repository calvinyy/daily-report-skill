import json
import sys
import types
from pathlib import Path

import work_report.cli as cli
from work_report.cli import main, parse_args
from work_report.config import DEFAULT_CONFIG, load_config, merge_cli_overrides
from work_report.models import ReportKind


def test_load_config_merges_user_values(tmp_path: Path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"daily_folder_name": "我的日报", "send_notification": False}, ensure_ascii=False),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config["daily_folder_name"] == "我的日报"
    assert config["weekly_folder_name"] == DEFAULT_CONFIG["weekly_folder_name"]
    assert config["send_notification"] is False


def test_load_config_migrates_legacy_folder_name(tmp_path: Path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"folder_name": "旧目录"}, ensure_ascii=False), encoding="utf-8")

    config = load_config(config_path)

    assert config["daily_folder_name"] == "旧目录"
    assert config["weekly_folder_name"] == "旧目录"


def test_default_config_includes_accio_paths():
    assert DEFAULT_CONFIG["accio_paths"] == [
        "~/.accio/history.jsonl",
        "~/.accio/tasks.jsonl",
        "~/Library/Application Support/Accio/history.jsonl",
    ]


def test_load_config_list_mutation_does_not_mutate_default_config(tmp_path: Path):
    config = load_config(tmp_path / "missing.json")

    config["enabled_sources"].append("temporary")
    config["accio_paths"].append("/tmp/accio.jsonl")

    assert "temporary" not in DEFAULT_CONFIG["enabled_sources"]
    assert "/tmp/accio.jsonl" not in DEFAULT_CONFIG["accio_paths"]


def test_parse_args_supports_weekly_kind_and_dry_run():
    args = parse_args(["--kind", "weekly", "--date", "2026-05-14", "--dry-run", "--no-ai"])

    assert args.kind == ReportKind.WEEKLY
    assert args.date_value == "2026-05-14"
    assert args.dry_run is True
    assert args.no_ai is True


def test_merge_cli_overrides_updates_folder_and_sources():
    args = parse_args(
        [
            "--kind",
            "daily",
            "--folder-name",
            "日报输出",
            "--source",
            "codex",
            "--source",
            "feishu",
            "--no-notify",
        ]
    )

    config = merge_cli_overrides(dict(DEFAULT_CONFIG), args)

    assert config["daily_folder_name"] == "日报输出"
    assert config["enabled_sources"] == ["codex", "feishu"]
    assert config["send_notification"] is False


def test_merge_cli_overrides_sets_raw_bundle_path():
    args = parse_args(["--raw-bundle-path", "/tmp/work-report-bundle.json"])

    config = merge_cli_overrides(dict(DEFAULT_CONFIG), args)

    assert config["include_raw_bundle_path"] == "/tmp/work-report-bundle.json"


def test_merge_cli_overrides_ai_cli_overrides_effective_claude_cli():
    args = parse_args(["--ai-cli", "/cli/claude"])
    config = merge_cli_overrides({**DEFAULT_CONFIG, "claude_cli": "/config/claude"}, args)

    assert config["ai_cli"] == "/cli/claude"
    assert config["claude_cli"] == "/cli/claude"


def test_main_install_check_resolves_binaries_and_uses_install_branch(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"lark_cli": "/custom/lark", "codex_cli": "/custom/codex", "ai_cli": "/custom/claude"}),
        encoding="utf-8",
    )
    find_calls = []
    install_call = {}
    workflow = types.ModuleType("work_report.workflow")

    def fake_find_binary(configured, name, extra_paths):
        find_calls.append((configured, name, extra_paths))
        return f"/resolved/{name}"

    def fake_run_install_check(config, lark_cli, codex_cli, claude_cli):
        install_call.update(
            config=config,
            lark_cli=lark_cli,
            codex_cli=codex_cli,
            claude_cli=claude_cli,
        )
        return 17

    def fake_run_workflow(**_kwargs):
        raise AssertionError("install-check should not run workflow")

    workflow.run_install_check = fake_run_install_check
    workflow.run_workflow = fake_run_workflow
    monkeypatch.setattr(cli, "find_binary", fake_find_binary)
    monkeypatch.setitem(sys.modules, "work_report.workflow", workflow)

    code = main(["--config", str(config_path), "--install-check"])

    assert code == 17
    assert find_calls == [
        ("/custom/lark", "lark-cli", ["/opt/homebrew/bin/lark-cli"]),
        ("/custom/codex", "codex", ["/opt/homebrew/bin/codex"]),
        ("/custom/claude", "claude", [str(Path.home() / ".local" / "bin" / "claude")]),
    ]
    assert install_call["lark_cli"] == "/resolved/lark-cli"
    assert install_call["codex_cli"] == "/resolved/codex"
    assert install_call["claude_cli"] == "/resolved/claude"
