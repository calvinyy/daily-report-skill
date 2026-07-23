# AGENTS.md

## 项目是什么

这是一个 Codex / Claude Code 的 skill 仓库：`daily-report`。它从本地活动来源
（Codex/Claude 会话、Accio 记录、飞书消息/日历/纪要、浏览器历史、本地 git commit）
生成结构化的飞书（Feishu/Lark）日报、周报，并写入飞书云文档；还能生成一份独立的
"晨间简报"（今日日程 + TODO + 待跟进 + 昨日日报指针）。仓库同时打包了给非技术同事看的
安装引导文档，用于把 `daily-report/` 目录分发/安装到别人的 Codex 或 Claude Code
skills 目录里使用。

## 技术栈

- 核心脚本是**纯 Python 标准库**实现，零第三方运行时依赖（改代码时保持这一点，
  方便直接复制分发，不要引入 pip 包）。
- 依赖两个外部 CLI：`lark-cli`（飞书读写，需已登录鉴权）、`codex` / `claude`
  （AI 摘要，Codex 优先、Claude 兜底、`--no-ai` 走模板格式化）。
- 测试用 pytest（仓库本身不提供 pytest，需要自行安装到某个 Python 环境）。

## 常用命令

```bash
# 装依赖检查
python3 daily-report/scripts/generate_daily_report.py --install-check

# 预览（不写飞书）
python3 daily-report/scripts/generate_daily_report.py --kind daily --dry-run
python3 daily-report/scripts/generate_daily_report.py --kind weekly --dry-run

# 正式生成并写入飞书
python3 daily-report/scripts/generate_daily_report.py --kind daily
python3 daily-report/scripts/generate_daily_report.py --kind weekly

# 生成配置模板
python3 daily-report/scripts/generate_daily_report.py --init-config

# 晨间简报（独立入口，打印 Markdown 到 stdout）
python3 daily-report/scripts/morning_briefing.py [--date YYYY-MM-DD]

# 一键安装/更新到 ~/.codex/skills/daily-report（rsync --delete，会清掉目标目录里的手改内容）
bash daily-report/scripts/install.sh

# 跑测试（105 个用例，需要 work_report 包在 sys.path 上，用 -m pytest 从 scripts/ 目录跑）
cd daily-report/scripts && python3 -m pytest ../tests -q
```

## 目录结构要点

- `daily-report/SKILL.md` — skill manifest（Codex/Claude Code 共用），frontmatter
  的 `description` 是 agent 判断"要不要触发这个 skill"的依据；新增能力（如晨间简报）
  必须同步更新这段描述。
- `daily-report/agents/openai.yaml` — Codex 侧的展示名/默认 prompt。
- `daily-report/scripts/generate_daily_report.py` — 入口 thin wrapper，实际逻辑在
  `work_report.cli.main`。
- `daily-report/scripts/morning_briefing.py` — 晨间简报独立入口。
- `daily-report/scripts/install.sh` — 面向新机器/新用户的安装脚本。
- `daily-report/scripts/work_report/` — 核心包：`cli.py`（参数解析）、`config.py`
  （配置读写/合并 CLI 覆盖）、`models.py`（`ReportKind` 等数据模型）、
  `collector_registry.py` + `collectors/`（codex/claude/accio/browser/lark 等采集器）、
  `lark_client.py` + `lark_writer.py`（飞书 v2 docs API 读写，幂等 str_replace）、
  `summarizer.py`（AI 摘要三级 fallback）、`net_env.py`（子进程代理环境变量处理）、
  `watchdog.py`（token 过期/漏报警报）、`workflow.py`（编排）。
- `daily-report/tests/` — pytest 用例，逐模块覆盖，无 `conftest.py`。
- `README.md`、`START_HERE.{html,docx,txt}`、`INSTALLATION_CN.{md,html,docx,rtf,txt}`
  — 面向非技术同事的安装引导，多格式并存（英文 README/SKILL.md + 中文安装文档）。
- `daily-report-skill.zip` — 分发用打包快照，**当前落后于最新源码**（最近几次重构
  未随之更新），别把它当最新代码的依据。

## 仓库特有约定/坑

- 用户配置在仓库外的 `~/.daily-report-skill/config.json`，日志在
  `~/.daily-report-skill/report.log`；不要把它们提交进仓库。
- `net_env.py` 记录了一次真实故障（2026-06-03）：`lark-cli` 必须**剥离**代理环境变量
  （飞书走国内直连），而 `claude -p` 要先探活代理端口再决定继承还是剥离，`codex`
  则保持继承环境不变。改动子进程调用时保持这个区分，不要一刀切处理代理变量。
- 报告写入是幂等的：同一天/同一周重跑会按 section 标题做 `str_replace` 替换而不是
  重复追加；标题格式必须和 `SKILL.md`「Report Behavior」里写的完全一致，否则会被
  当成新的一天/周。
- AI 摘要顺序固定：`codex exec --skip-git-repo-check --ephemeral` 优先，失败/超时
  才 fallback 到 `claude -p`，两者都不可用时报错退出并提示登录 Codex（除非显式
  `--no-ai`）。
- 面向用户可见的行为变化（新增参数、新增数据源、报告格式等）习惯上会同步改多份文档
  （`README.md`、`SKILL.md`、`INSTALLATION_CN.*`、`START_HERE.*`），历史提交经常是
  这些文件一起改的。
