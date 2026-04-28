# Daily Report Skill

Codex skill for generating Feishu/Lark daily work reports from local activity sources.

## Start Here

For non-technical installation steps, open one of these files:

- `START_HERE.html`
- `START_HERE.docx`
- `START_HERE.txt`

## What It Does

- Collects Codex sessions, Claude Code sessions, Feishu messages, Feishu calendar events, Feishu minutes, and Chrome history.
- Generates a structured daily report.
- Creates or reuses a Feishu Drive folder named `周报记录`.
- Creates or reuses a weekly Feishu document.
- Writes the daily section and maintains a `改动记录` section.
- Optionally sends a Feishu notification.

## Skill Folder

The actual Codex skill is in:

```text
daily-report/
```

Install it by copying `daily-report/` into:

```text
~/.codex/skills/
```

Then restart Codex.
