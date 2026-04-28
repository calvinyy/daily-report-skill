---
name: daily-report
description: Generate, preview, troubleshoot, or set up Feishu/Lark daily work reports and weekly report documents from local activity sources. Use when the user asks for 日报, daily report, work report, 周报记录, Feishu report automation, collecting Codex or Claude sessions, summarizing daily work, writing a report into a Lark document, or helping coworkers install the daily report workflow.
---

# Daily Report

## Overview

Use this skill to generate a daily work report, write it into a weekly Feishu/Lark document, and optionally notify the user. The bundled script collects local Codex sessions, Claude Code history, Feishu messages/calendar/minutes, and Chrome history, then summarizes the work with a local Claude-compatible CLI if available.

## Quick Workflow

1. Locate this skill directory and use `scripts/generate_daily_report.py`.
2. Check installation before editing Feishu:
   ```bash
   python3 /path/to/daily-report/scripts/generate_daily_report.py --install-check
   ```
3. Preview today's report without writing to Feishu:
   ```bash
   python3 /path/to/daily-report/scripts/generate_daily_report.py --dry-run
   ```
4. Generate and write today's report:
   ```bash
   python3 /path/to/daily-report/scripts/generate_daily_report.py
   ```
5. Generate a specific date:
   ```bash
   python3 /path/to/daily-report/scripts/generate_daily_report.py 2026-04-28
   ```

After a successful write, always return the Feishu document link shown in the script log.

## Configuration

Create a user config when first setting up:

```bash
python3 /path/to/daily-report/scripts/generate_daily_report.py --init-config
```

The config is written to `~/.daily-report-skill/config.json`. Important fields:

- `folder_name`: Feishu Drive folder for weekly docs. Default: `周报记录`.
- `notify_user_id`: Feishu `open_id` to notify. If blank, the script uses the current authorized user when available.
- `notify_as`: `user` or `bot`. Prefer `user` for individual installs.
- `send_notification`: set `false` to write docs without sending a message.
- `lark_cli`: optional explicit path to `lark-cli`.
- `ai_cli`: optional explicit path to a Claude-compatible CLI.
- `ai_enabled`: set `false` to skip AI summarization and use fallback formatting.
- `chrome_profile`: Chrome profile folder, usually `Default` or `Profile 1`.
- `skip_domains`: domains excluded from browser-history summaries.

Command-line flags override config values for one run: `--folder-name`, `--notify-user-id`, `--lark-cli`, `--ai-cli`, `--no-ai`, `--no-notify`.

## Feishu Requirements

The script depends on `lark-cli` authenticated as the user. The login should include these domains:

```bash
lark-cli auth login --domain im,calendar,drive,docs,contact,minutes --no-wait --json
```

If the user cannot authorize or the token expired, help them rerun login and then rerun `--install-check`.

## Report Behavior

The script creates or reuses:

- Feishu Drive folder: `folder_name`
- Weekly document title: `第{ISO周数}周 MM/DD-MM/DD`
- Daily section title: `YYYY年MM月DD日（周X）`

For each successful document write, the script also adds or updates a `改动记录` section with edit time, editor name from Feishu auth, and a concise change summary.

## Data Sources

The script reads local and Feishu data for the target date:

- `~/.codex/state_5.sqlite` for Codex sessions
- `~/.claude/history.jsonl` for Claude Code sessions
- Feishu message search, calendar agenda, and minutes search through `lark-cli`
- Chrome history from `~/Library/Application Support/Google/Chrome/{chrome_profile}/History`

Chrome history is copied to a temporary SQLite file before reading so it can work while Chrome is open.

## Troubleshooting

- Missing `lark-cli`: ask the user to install `@larksuite/cli`, then run `lark-cli --version`.
- Invalid Feishu token: run `lark-cli auth status`, then rerun `lark-cli auth login ...`.
- No AI summary: install or configure a Claude-compatible CLI, or run with `--no-ai`.
- Empty Chrome history: check `chrome_profile` in config.
- Duplicate daily sections: rerun after confirming the existing section heading matches `## YYYY年MM月DD日（周X）`.
- Permission errors on Feishu docs: rerun auth with all required domains and use `--install-check`.
