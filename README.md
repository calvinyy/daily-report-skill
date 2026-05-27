# Daily Report Skill

Codex / Claude Code skill for generating Feishu/Lark daily work reports from local activity sources.

## Start Here

For non-technical installation steps, open one of these files:

- `START_HERE.html`
- `START_HERE.docx`
- `START_HERE.txt`

## What It Does

- Collects Codex sessions, Claude Code sessions, Feishu messages, Feishu calendar events, Feishu minutes, and Chrome history.
- Generates a structured daily report with Codex first and Claude Code as fallback.
- Records **every** Claude/Codex session individually (no omissions); folds recurring hourly Codex automations into one entry per task.
- Fails with an actionable Codex setup message if neither Codex nor Claude is available, unless `--no-ai` is explicitly used.
- Creates or reuses a Feishu Drive folder named `周报记录`.
- Creates or reuses a weekly Feishu document.
- Writes the daily section via the **v2** `docs +update` API and re-runs idempotently in place (str_replace, with append on first write).
- Optionally sends a Feishu notification.

## Skill Folder

The actual skill is in:

```text
daily-report/
```

Install it by copying `daily-report/` into your agent's skills directory, then restart the agent:

- **Codex**: `~/.codex/skills/`
- **Claude Code**: `~/.claude/skills/`

The `SKILL.md` manifest works for both. Once installed, ask the agent
"用 daily-report 生成今天的日报" (Claude Code also accepts `/daily-report`).
See `INSTALLATION_CN.md` for the full step-by-step guide, including a dedicated
Claude Code section.
