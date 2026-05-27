---
name: daily-report
description: Generate, preview, troubleshoot, or set up Feishu/Lark daily work reports and weekly report documents from local activity sources. Use when the user asks for 日报, daily report, work report, 周报记录, Feishu report automation, collecting Codex or Claude sessions, summarizing daily work, writing a report into a Lark document, or helping coworkers install the daily report workflow.
---

# Daily Report

## Overview

Use this skill to generate a daily work report, write it into a weekly Feishu/Lark document, and optionally notify the user. The bundled script collects local Codex sessions, Claude Code history, Feishu messages/calendar/minutes, and Chrome history, then summarizes the work with Codex first and Claude Code as a fallback.

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
- `codex_cli`: optional explicit path to the Codex CLI. Preferred summarizer.
- `claude_cli`: optional explicit path to the Claude Code CLI. Fallback summarizer.
- `ai_cli`: deprecated alias for `claude_cli`.
- `ai_enabled`: set `false` to skip Codex/Claude summarization and use fallback formatting.
- `chrome_profile`: Chrome profile folder, usually `Default` or `Profile 1`.
- `skip_domains`: domains excluded from browser-history summaries.

Command-line flags override config values for one run: `--folder-name`, `--notify-user-id`, `--lark-cli`, `--codex-cli`, `--claude-cli`, `--ai-cli`, `--no-ai`, `--no-notify`.

Default summarization order:

1. Use `codex exec --skip-git-repo-check --ephemeral` and read the final answer through `--output-last-message`.
2. If Codex is missing, not logged in, times out, or returns no summary, fall back to `claude -p --output-format text`.
3. If both are unavailable, exit with an explicit error and advise the user to install/open Codex, run `codex login status`, run `codex login` if needed, or temporarily use `--no-ai` for a basic raw-record report.

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
- Daily section title: `## YYYY年MM月DD日（周X）`

### Faithful per-session recording

Every Claude Code session and every Codex session for the day is enumerated
individually in the report — never collapsed into a single vague line. The
prompt keeps each Claude session's full instruction arc (opener + follow-ups)
and asks the model to judge the real goal/output from the whole arc. Recurring
hourly Codex automations (titles starting `Automation:`) are the one exception:
they are folded by automation ID into one entry with a run count, time span, and
latest progress, so background loops don't drown out genuine interactive work.

### Idempotent re-runs (v2 docs API)

Writing the daily section uses the **v2** `docs +update` API only:

- **First write of the day** → `--command append --doc-format markdown`.
- **Re-run for the same day** → `--command str_replace --doc-format markdown`
  with the `前缀...后缀` ellipsis pattern `## <日期>（周X）...---`, replacing the
  whole day block in place. str_replace returns no `updated_blocks_count`, so
  success is detected via `data.result == "success"`; a missing pattern returns
  `result: "failed"` and the code falls back to `append`.

This stays atomic only because the date heading is the block's **only** `##`.
The script normalizes the model output before writing: any stray `##` is demoted
to `###`, and stray horizontal rules (`---`/`***`/`___`) are dropped so the only
`---` in the block is the trailing separator the pattern keys off. The four body
sections (今日工作总结 / 主要项目进展 / AI 会话明细 / 沟通&会议) are therefore
always `###`.

## Data Sources

The script reads local and Feishu data for the target date:

- `~/.codex/state_5.sqlite` for Codex sessions (interactive kept individually, automations folded by ID)
- `~/.claude/history.jsonl` for Claude Code sessions (grouped by `sessionId`, full prompt arc kept)
- Feishu message search and calendar agenda (with video-conference flag) through `lark-cli`
- Feishu minutes (妙记) via `minutes +search` as **both owner and participant** (deduped by token); titles read from the `display_info` field
- Chrome history from `~/Library/Application Support/Google/Chrome/{chrome_profile}/History`

Chrome history is copied to a temporary SQLite file before reading so it can work while Chrome is open.

## Troubleshooting

- Missing `lark-cli`: ask the user to install `@larksuite/cli`, then run `lark-cli --version`.
- Invalid Feishu token: run `lark-cli auth status`, then rerun `lark-cli auth login ...`.
- No AI summary: prioritize installing/opening Codex and running `codex login status`; Claude Code CLI is only the fallback. Use `--no-ai` only when a basic raw-record report is acceptable.
- Empty Chrome history: check `chrome_profile` in config.
- Duplicate daily sections on re-run: caused by inner `##` headings (the str_replace `## …（周X）...---` pattern is bounded by the first `##`/`---`). The script demotes `##`→`###` and strips stray `---` automatically; if you hand-edit the doc, keep the date heading as the block's only `##` and one trailing `---`.
- Permission errors on Feishu docs: rerun auth with all required domains and use `--install-check`.
