---
name: daily-report
description: Generate, preview, troubleshoot, or set up Feishu/Lark daily work reports and weekly report documents from local activity sources. Use when the user asks for 日报, daily report, work report, 周报记录, Feishu report automation, collecting Codex or Claude sessions, summarizing daily work, writing a report into a Lark document, a 晨间简报 / morning briefing (yesterday recap + today's TODOs + calendar reminders), or helping coworkers install the daily report workflow.
---

# Daily Report

## Overview

Use this skill to generate Chinese daily or weekly work reports, write them into Feishu/Lark folders, and optionally notify the user. The bundled script collects local Codex sessions, Claude Code history, Feishu messages/calendar/minutes, browser activity, and related local sources, then summarizes the work with local Codex CLI first, Claude Code CLI second, and template formatting as the final fallback.

## Date Range Defaults

When the user asks to update daily reports for a relative range, such as "过去7天", "过去一周", or "最近几天", exclude the current date by default. Treat the daily-report range as the most recent fully completed days, ending yesterday, unless the user explicitly asks to include today, today's report, or the current date.

This completed-day default only changes which daily dates are generated. Weekly reports still use the existing natural ISO week window from Monday through Sunday; do not reinterpret weekly reports as a rolling seven-day window.

Examples:

- If today is 2026-06-11, "更新过去7天的日报" means 2026-06-04 through 2026-06-10.
- If today is 2026-06-11, "更新过去7天的周报和日报" updates daily reports for 2026-06-04 through 2026-06-10, then updates the natural Monday-Sunday weekly reports touched by that completed-day range.
- If the user says "包括今天", "今天也更新", or asks for today's daily report, include the current date.

## Quick Workflow

1. Check installation:
   ```bash
   python3 ~/.codex/skills/daily-report/scripts/generate_daily_report.py --install-check
   ```
2. Preview today's daily report:
   ```bash
   python3 ~/.codex/skills/daily-report/scripts/generate_daily_report.py --kind daily --dry-run
   ```
3. Preview this week's weekly report:
   ```bash
   python3 ~/.codex/skills/daily-report/scripts/generate_daily_report.py --kind weekly --dry-run
   ```
4. Generate and write today's daily report:
   ```bash
   python3 ~/.codex/skills/daily-report/scripts/generate_daily_report.py --kind daily
   ```
5. Generate and write this week's weekly report:
   ```bash
   python3 ~/.codex/skills/daily-report/scripts/generate_daily_report.py --kind weekly
   ```

After a successful write, always return the Feishu document link shown in the script log.

## Install For Another User

From a cloned or copied skill folder, run:

```bash
bash scripts/install.sh
```

The installer copies this skill to `~/.codex/skills/daily-report`, creates `~/.daily-report-skill/config.json` if missing, and runs the install check. After that, the user should complete the Feishu authorization below if `lark-cli` is not already authenticated.

## Configuration

Create a user config when first setting up:

```bash
python3 ~/.codex/skills/daily-report/scripts/generate_daily_report.py --init-config
```

The config is written to `~/.daily-report-skill/config.json`. Important fields:

- `daily_folder_name`: Feishu Drive folder for daily reports. Default: `日报记录`.
- `weekly_folder_name`: Feishu Drive folder for weekly reports. Default: `周报记录`.
- `enabled_sources`: source collectors to use, such as `codex`, `claude`, `accio`, `feishu`, `browser`, `computer`, and `git`.
- `accio_paths`: Accio JSONL export paths to collect.
- `notify_user_id`: Feishu `open_id` to notify. If blank, the script uses the current authorized user when available.
- `notify_as`: `user` or `bot`. Prefer `user` for individual installs.
- `send_notification`: set `false` to write docs without sending a message.
- `lark_cli`: optional explicit path to `lark-cli`.
- `codex_cli`: optional explicit path to `codex`.
- `codex_model`: model passed to `codex exec`; default is `gpt-5.4-mini`. Set to empty string to use Codex default config.
- `claude_cli`: optional explicit path to Claude Code CLI fallback.
- `ai_cli`: legacy alias for `claude_cli`.
- `ai_enabled`: set `false` to skip AI summarization and use fallback formatting.
- `chrome_profiles`: Chrome profile folders, usually `Default` or `Profile 1`.
- `skip_domains`: domains excluded from browser-history summaries.

Command-line flags override config values for one run: `--kind`, `--date`, `--folder-name`, `--source`, `--raw-bundle-path`, `--notify-user-id`, `--lark-cli`, `--codex-cli`, `--codex-model`, `--claude-cli`, `--ai-cli`, `--no-ai`, `--no-notify`. Use `--raw-bundle-path` to write the normalized source bundle JSON to the given path.

## Feishu Requirements

The script depends on `lark-cli` authenticated as the user. The login should include these domains:

```bash
lark-cli auth login --domain im,calendar,drive,docs,contact,minutes --no-wait --json
lark-cli auth login --scope "search:docs:read task:task:read docs:document.comment:read" --no-wait --json
```

If the user cannot authorize or the token expired, help them rerun login and then rerun `--install-check`.

## Report Behavior

The script creates or reuses:

- Daily Feishu Drive folder: `daily_folder_name`
- Weekly Feishu Drive folder: `weekly_folder_name`
- Daily document title: `YYYY年第WW周日报 MM/DD`
- Daily section heading: `YYYY年MM月DD日（周X）`
- Weekly document title: `YYYY年第WW周周报 MM/DD-MM/DD`
- Weekly section heading: `YYYY年第WW周工作周报`

For each successful document write, the script replaces the matching day or week section so reruns update the same report.

## Expanded Report Sources

The workflow collects and normalizes local and Feishu data for the target date or week:

- Codex sessions, rollout summaries, tool traces, and local git commits.
- Claude history from `~/.claude/history.jsonl`.
- Accio records from configured `accio_paths`; missing paths are included as `需确认`.
- Feishu messages, calendar events, minutes, recently edited/commented docs, and related tasks through `lark-cli`.
- Chrome history from configured `chrome_profiles`.
- Browser Use and Computer Use tool traces found inside Codex rollout files.

Chrome history is copied to a temporary SQLite file before reading so it can work while Chrome is open. Required Feishu authorization is listed under Feishu Requirements. Daily reports emphasize execution progress. Weekly reports emphasize project outcomes, review of issues, risks, and next-week plans.

## Morning Briefing (晨间简报)

Besides the daily/weekly reports, this skill can produce a **morning briefing** — a
short daily digest the user reads first thing (typically pushed at 10:00). It answers
"what happened yesterday, what's on my plate today, and what am I committed to at a
set time".

Run it:

```bash
python3 ~/.codex/skills/daily-report/scripts/morning_briefing.py
# optional: --date 2026-07-10
```

It assembles four blocks and prints clean Markdown to stdout (so a cron/chat bot can
deliver it verbatim):

1. **今日时间提醒** — today's Feishu calendar events (`lark-cli calendar +agenda`), with
   location / VC link, and a ⚠️ flag when an invite is still `needs_action`.
2. **今日 TODO** — open items from the user's Feishu todo note (the latest dated
   sections; struck-through `<del>` items are treated as done).
3. **待跟进** — pending "别人找我说的事" and rolling 待办 from the local work digest.
4. **昨日日报** — pointer to yesterday's daily report.

Configure under a `morning_briefing` key in the config file:

```json
"morning_briefing": {
  "todo_doc_token": "CHgcd78UFohk66xACkUcUkNPnb9",
  "digest_dir": "~/Riemann/工作整理"
}
```

The local work digest at `digest_dir` (`工作整理总览.md`, `日常事项/YYYY-MM.md`, `周报/`)
is the long-term record of the user's Feishu work; keep it updated when generating
reports. Scheduling is external (e.g. a cc-connect cron at `0 10 * * *`); note that a
local cron only fires while the machine is awake and the daemon is running — move it to
a server-side automation if punctual delivery is required.

## Troubleshooting

- Missing `lark-cli`: ask the user to install `@larksuite/cli`, then run `lark-cli --version`.
- Invalid Feishu token: run `lark-cli auth status`, then rerun `lark-cli auth login ...`.
- No AI summary: install or configure `codex` and/or Claude Code CLI, or run with `--no-ai`.
- Empty Chrome history: check `chrome_profiles` in config.
- Duplicate report sections: rerun after confirming the existing section heading matches the daily or weekly section heading.
- Permission errors on Feishu docs: rerun auth with all required domains and use `--install-check`.
