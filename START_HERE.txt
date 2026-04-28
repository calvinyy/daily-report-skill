# 日报 Skill 安装指南

版本日期: 2026-04-28

这份指南给不会写代码的同事使用。照着做完以后，就可以让 Codex 生成每日工作日报，并自动写入飞书的 `周报记录` 文件夹。

## 这个 Skill 能做什么

- 汇总当天 Codex 会话、Claude Code 会话、飞书消息、飞书日历、飞书会议纪要、Chrome 浏览记录。
- 生成一份结构化日报。
- 自动创建或复用飞书云文档文件夹 `周报记录`。
- 自动创建或复用本周文档，例如 `第18周 04/27-05/03`。
- 自动写入当天日报区段，例如 `2026年04月28日（周二）`。
- 自动维护 `改动记录`，记录编辑时间、编辑人和改动摘要。
- 可选发送飞书通知给自己。

## 安装前准备

请先确认电脑是 macOS，并且已经安装 Codex。这个版本目前按 macOS + Chrome + 飞书设计。

需要准备:

- 飞书账号。
- 可以打开“终端”应用。
- 可以安装 Node.js / npm。
- Codex CLI。日报默认优先使用 Codex 生成总结。
- 可选: Claude Code CLI。只有 Codex 不可用时才作为备用。

## 第 1 步: 安装 Daily Report Skill

拿到安装包后，通常会看到一个文件夹:

```text
daily-report
```

把它放到 Codex 的 skills 目录。

### 方法 A: 用 Finder 安装

1. 双击解压安装包。
2. 打开 Finder。
3. 按 `Command + Shift + G`。
4. 输入:
   ```text
   ~/.codex/skills
   ```
5. 如果提示目录不存在，先输入:
   ```text
   ~/.codex
   ```
   然后新建一个名为 `skills` 的文件夹。
6. 把解压出来的 `daily-report` 文件夹拖进去。
7. 完全退出 Codex，再重新打开。

### 方法 B: 复制命令安装

如果 `daily-report` 文件夹在“下载”目录，打开“终端”，粘贴下面两行:

```bash
mkdir -p ~/.codex/skills
cp -R ~/Downloads/daily-report ~/.codex/skills/
```

然后重启 Codex。

## 第 2 步: 安装 lark-cli

`lark-cli` 是连接飞书的命令行工具。Daily Report Skill 需要用它读取日历、消息、会议纪要，并写入云文档。

### 2.1 检查是否已经安装 Node.js

打开“终端”，粘贴:

```bash
node -v
npm -v
```

如果两行都显示版本号，例如 `v20.x.x` 和 `10.x.x`，说明已经有 Node.js，可以继续下一步。

如果提示 `command not found`，请先安装 Node.js:

1. 打开 [Node.js 官网](https://nodejs.org/)。
2. 下载 LTS 版本。
3. 一路点“继续 / 安装”。
4. 安装完成后，重新打开“终端”，再运行:
   ```bash
   node -v
   npm -v
   ```

### 2.2 安装飞书 CLI

在“终端”粘贴:

```bash
npm install -g @larksuite/cli
```

安装完成后检查:

```bash
lark-cli --version
```

能看到版本号就表示安装成功。

## 第 3 步: 登录飞书授权

在“终端”粘贴下面这行:

```bash
lark-cli auth login --domain im,calendar,drive,docs,contact,minutes
```

终端会显示一个登录链接或验证码。按照提示打开浏览器，登录飞书并同意授权。

授权完成后检查:

```bash
lark-cli auth status
```

看到 `"tokenStatus": "valid"` 就表示成功。

## 第 4 步: 做安装检查

打开“终端”，粘贴:

```bash
python3 ~/.codex/skills/daily-report/scripts/generate_daily_report.py --install-check
```

成功时会看到类似:

```text
Daily Report Skill 安装检查
- Python: 3.9.6
- lark-cli: /opt/homebrew/bin/lark-cli
- 飞书授权: valid
- 飞书用户: 你的名字
```

如果这里不成功，先不要继续生成日报。看本文末尾“常见问题”。

## 第 5 步: 先预览，不写入飞书

第一次建议先运行预览:

```bash
python3 ~/.codex/skills/daily-report/scripts/generate_daily_report.py --dry-run
```

预览时默认会优先调用 Codex 生成总结。如果 Codex 不可用，会自动尝试 Claude Code CLI。

如果 Codex 和 Claude 都暂时不可用，但你只想先看基础原始记录，可以用:

```bash
python3 ~/.codex/skills/daily-report/scripts/generate_daily_report.py --dry-run --no-ai
```

预览成功后，终端会打印一份日报内容，但不会写入飞书。

## 第 6 步: 正式生成今天日报

确认预览没问题后，运行:

```bash
python3 ~/.codex/skills/daily-report/scripts/generate_daily_report.py
```

成功后会显示类似:

```text
完成！文档: https://feishu.cn/docx/xxxxxxxx
```

打开这个链接就能看到日报。

## 第 7 步: 在 Codex 里使用

重启 Codex 后，可以直接对 Codex 说:

```text
用 daily-report 生成今天的日报
```

也可以说:

```text
用 daily-report 预览一下今天日报，不要写飞书
```

生成指定日期:

```text
用 daily-report 生成 2026-04-28 的日报
```

## 可选配置

第一次可以创建配置文件:

```bash
python3 ~/.codex/skills/daily-report/scripts/generate_daily_report.py --init-config
```

配置文件位置:

```text
~/.daily-report-skill/config.json
```

常用配置:

- `folder_name`: 飞书文件夹名称，默认 `周报记录`。
- `send_notification`: 是否发送飞书通知，默认 `true`。
- `notify_user_id`: 要通知的飞书 open_id。留空时默认通知当前授权用户。
- `codex_cli`: Codex CLI 路径。通常不需要手动填写，脚本会自动寻找。
- `claude_cli`: Claude Code CLI 路径。只作为 Codex 不可用时的备用。
- `ai_enabled`: 是否使用 Codex / Claude 总结，默认 `true`。
- `chrome_profile`: Chrome 用户目录。常见值是 `Default` 或 `Profile 1`。

如果你不想收到飞书通知，可以运行:

```bash
python3 ~/.codex/skills/daily-report/scripts/generate_daily_report.py --no-notify
```

## 可选: 每天自动运行

建议先手动成功运行 2 次，再设置自动运行。

如果想让它每天 18:30 自动生成日报，把下面整段复制到“终端”:

```bash
mkdir -p ~/Library/LaunchAgents
cat > ~/Library/LaunchAgents/com.company.daily-report.plist <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.company.daily-report</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>$HOME/.codex/skills/daily-report/scripts/generate_daily_report.py</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>18</integer>
    <key>Minute</key>
    <integer>30</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>$HOME/.daily-report-skill/launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>$HOME/.daily-report-skill/launchd.err.log</string>
</dict>
</plist>
PLIST
launchctl unload ~/Library/LaunchAgents/com.company.daily-report.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.company.daily-report.plist
```

检查是否已加载:

```bash
launchctl list | grep daily-report
```

取消自动运行:

```bash
launchctl unload ~/Library/LaunchAgents/com.company.daily-report.plist
rm ~/Library/LaunchAgents/com.company.daily-report.plist
```

## 常见问题

### 1. `lark-cli: command not found`

说明飞书 CLI 没安装成功。重新运行:

```bash
npm install -g @larksuite/cli
lark-cli --version
```

如果仍然失败，关闭“终端”后重新打开再试。

### 2. `tokenStatus` 不是 `valid`

说明飞书登录过期或授权不完整。重新运行:

```bash
lark-cli auth login --domain im,calendar,drive,docs,contact,minutes
lark-cli auth status
```

### 3. 飞书文档没有写入

先运行:

```bash
python3 ~/.codex/skills/daily-report/scripts/generate_daily_report.py --install-check
```

如果授权正常，再检查飞书是否允许创建云文档，以及账号是否有 Drive / Docs 权限。

### 4. 提示 Codex 和 Claude 都不可用

日报默认优先使用 Codex 生成总结，Claude Code CLI 只是备用。如果两个都不可用，脚本会停止并提示开通或登录 Codex。

请先检查 Codex:

```bash
codex login status
```

如果没有登录，运行:

```bash
codex login
```

再验证 Codex 能否工作:

```bash
codex exec --skip-git-repo-check --ephemeral "只输出 OK"
```

如果公司暂时没有开通 Codex，可以先安装并登录 Claude Code CLI 作为备用。

临时不用 AI，只生成基础版原始记录:


```bash
python3 ~/.codex/skills/daily-report/scripts/generate_daily_report.py --no-ai
```

### 5. Chrome 浏览记录为空

可能 Chrome 用户目录不是 `Default`。打开:

```text
~/Library/Application Support/Google/Chrome/
```

看看里面有没有 `Profile 1`、`Profile 2`。然后修改:

```text
~/.daily-report-skill/config.json
```

把 `chrome_profile` 改成对应目录名。

### 6. 日报重复出现

脚本会尝试替换同一天标题的区段。请确认飞书文档里的标题格式是:

```text
2026年04月28日（周二）
```

不要手动改成其他格式，否则脚本会认为这是新的一天并追加新区段。

### 7. 去哪里看日志

日志位置:

```text
~/.daily-report-skill/report.log
```

如果是自动运行，额外看:

```text
~/.daily-report-skill/launchd.out.log
~/.daily-report-skill/launchd.err.log
```

## 卸载

删除 skill:

```bash
rm -rf ~/.codex/skills/daily-report
```

删除配置和日志:

```bash
rm -rf ~/.daily-report-skill
```

如果设置过自动运行，先执行:

```bash
launchctl unload ~/Library/LaunchAgents/com.company.daily-report.plist
rm ~/Library/LaunchAgents/com.company.daily-report.plist
```

## 给管理员或推广人的说明

对外分发时，只需要把 `daily-report` 文件夹打包给同事。不要把个人的 `~/.daily-report-skill/config.json`、飞书 token、日志文件一起发出去。

建议推广话术:

```text
装好这个 Daily Report Skill 后，在 Codex 里说“用 daily-report 生成今天日报”，它会自动把当天工作记录汇总到飞书周报文档。
```
