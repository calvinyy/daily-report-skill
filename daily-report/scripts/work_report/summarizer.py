from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from work_report.models import ActivityBundle, DateWindow, ReportKind, SourceRecord
from work_report.prompts import DAILY_SECTIONS, WEEKLY_SECTIONS, build_summary_prompt


Runner = Callable[..., subprocess.CompletedProcess[str]]
AI_SOURCES = {"codex", "claude", "accio", "computer", "browser"}
PROJECT_SOURCES = {"codex", "claude", "git", "accio"}
PROJECT_KEYWORDS = ("开发", "调研", "文档", "排障", "发布", "实现", "修复", "测试", "项目", "需求", "产品", "技术")
MEETING_KEYWORDS = ("会议", "评审", "沟通", "对齐", "讨论", "决策", "结论", "相关人")
TODO_KEYWORDS = ("待办", "后续", "下一步", "跟进", "需确认", "待确认", "阻塞", "失败", "缺失", "missing", "partial")
REUSABLE_KEYWORDS = ("文档", "规范", "流程", "复盘", "知识库", "沉淀", "总结", "方案", "设计", "SOP", "KB")


def short_cli_error(exc: BaseException | subprocess.CompletedProcess[str], limit: int = 500) -> str:
    if isinstance(exc, subprocess.CompletedProcess):
        text = exc.stderr or exc.stdout or f"exit code {exc.returncode}"
    elif isinstance(exc, subprocess.TimeoutExpired):
        text = f"command timed out after {exc.timeout}s"
    else:
        text = str(exc)
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def summarize_with_codex(
    prompt: str,
    codex_cli: str,
    model: str = "",
    timeout: int = 180,
    runner: Runner = subprocess.run,
) -> str:
    if not codex_cli:
        return ""
    command = [codex_cli, "exec"]
    if model:
        command.extend(["--model", model])
    command.append(prompt)
    completed = runner(command, capture_output=True, text=True, timeout=timeout, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"Codex CLI failed: {short_cli_error(completed)}")
    return completed.stdout.strip()


def summarize_with_claude(
    prompt: str,
    claude_cli: str,
    model: str = "",
    timeout: int = 180,
    runner: Runner = subprocess.run,
) -> str:
    if not claude_cli:
        return ""
    command = [claude_cli, "-p", prompt]
    if model:
        command.extend(["--model", model])
    completed = runner(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"Claude CLI failed: {short_cli_error(completed)}")
    return completed.stdout.strip()


def fallback_render(window: DateWindow, bundle: ActivityBundle, max_records: int = 180) -> str:
    records = bundle.sorted_records()[:max_records]
    is_weekly = window.kind == ReportKind.WEEKLY
    buckets = _fallback_buckets(records)
    summary_text = (
        _weekly_summary_paragraph(records, buckets, bundle.collection_status)
        if is_weekly
        else _daily_summary_paragraph(records, buckets, bundle.collection_status)
    )
    followup_title = "下周计划" if is_weekly else "后续事项"

    lines = [
        f"# {window.section_heading}",
        "",
        f"## {'本周工作总结' if is_weekly else '今日工作总结'}",
        summary_text,
        "",
        "## 主要项目进展",
        *_project_sections(buckets["project"], is_weekly=is_weekly),
        "",
        "## AI 工具协作",
        *_ai_summary_lines(buckets["ai"], is_weekly=is_weekly),
        "",
        "## 沟通 & 会议",
        *_communication_summary_lines(buckets["communication"], is_weekly=is_weekly),
        "",
        f"## {followup_title}",
        *_followup_lines(buckets["todo"], buckets["reusable"], is_weekly=is_weekly),
    ]
    return "\n".join(lines).rstrip() + "\n"


def summarize(
    window: DateWindow,
    bundle: ActivityBundle,
    config: dict[str, Any] | None = None,
    binaries: dict[str, str] | None = None,
    runner: Runner = subprocess.run,
) -> str:
    config = config or {}
    binaries = binaries or {}
    max_records = int(config.get("max_prompt_records") or 180)
    prompt = build_summary_prompt(window, bundle, max_records)
    errors: list[str] = []

    if config.get("ai_enabled", True):
        codex_cli = binaries.get("codex") or str(config.get("codex_cli") or "")
        if codex_cli:
            try:
                summary = summarize_with_codex(
                    prompt,
                    codex_cli=codex_cli,
                    model=str(config.get("codex_model") or ""),
                    runner=runner,
                )
                if summary and _has_required_report_shape(summary, window):
                    return summary
                if summary:
                    errors.append("Codex CLI returned invalid report shape")
            except Exception as exc:  # pragma: no cover - exercised through workflow logging.
                errors.append(short_cli_error(exc))

        claude_cli = binaries.get("claude") or str(config.get("claude_cli") or config.get("ai_cli") or "")
        if claude_cli:
            try:
                summary = summarize_with_claude(
                    prompt,
                    claude_cli=claude_cli,
                    model=str(config.get("claude_model") or ""),
                    runner=runner,
                )
                if summary and _has_required_report_shape(summary, window):
                    return summary
                if summary:
                    errors.append("Claude CLI returned invalid report shape")
            except Exception as exc:  # pragma: no cover - exercised through workflow logging.
                errors.append(short_cli_error(exc))

    rendered = fallback_render(window, bundle, max_records=max_records)
    if errors:
        rendered += "\n<!-- AI summarization fallback: " + " | ".join(errors) + " -->\n"
    return rendered


def _has_required_report_shape(markdown: str, window: DateWindow) -> bool:
    expected = WEEKLY_SECTIONS if window.kind == ReportKind.WEEKLY else DAILY_SECTIONS
    headings = [line.removeprefix("## ").strip() for line in markdown.splitlines() if line.startswith("## ")]
    return headings == expected


def _is_risk(record: SourceRecord) -> bool:
    text = " ".join([record.title, record.content, " ".join(record.tags)]).lower()
    if record.needs_confirmation:
        return True
    hard_failure_keywords = [
        "partial",
        "missing",
        "failed",
        "4030004",
        "lacks view or edit access",
        "未找到",
        "未能完成",
        "被拒绝",
        "权限不足",
        "无法访问",
        "写入失败",
        "采集失败",
        "启动失败",
        "安装失败",
        "更新失败",
        "执行失败",
        "返回错误",
    ]
    if any(keyword in text for keyword in hard_failure_keywords):
        return True
    unresolved_blocker = any(keyword in text for keyword in ["存在阻塞", "当前阻塞"])
    no_blocker = any(phrase in text for phrase in ["无阻塞", "未阻塞", "没有阻塞"])
    return unresolved_blocker and not no_blocker


def _is_meeting_or_communication(record: SourceRecord) -> bool:
    if record.people or record.source == "feishu":
        return True
    return _contains_any(" ".join(record.tags), MEETING_KEYWORDS)


def _is_ai_collaboration(record: SourceRecord) -> bool:
    return record.source in AI_SOURCES


def _is_project_progress(record: SourceRecord) -> bool:
    if record.source == "browser":
        return bool(record.project)
    text = " ".join([record.title, record.content, " ".join(record.tags), record.project])
    return bool(record.project) or _contains_any(text, PROJECT_KEYWORDS)


def _is_todo(record: SourceRecord) -> bool:
    if record.source == "browser" and not record.needs_confirmation and not record.project:
        return False
    text = " ".join([record.title, record.content, " ".join(record.tags)]).lower()
    return _is_risk(record) or _contains_any(text, TODO_KEYWORDS)


def _is_reusable_content(record: SourceRecord) -> bool:
    if record.source == "browser" and "feishu.cn" in record.title.lower():
        return False
    text = " ".join([record.title, record.content, " ".join(record.tags)])
    return _contains_any(text, REUSABLE_KEYWORDS)


def _fallback_buckets(records: Sequence[SourceRecord]) -> dict[str, list[SourceRecord]]:
    buckets: dict[str, list[SourceRecord]] = {
        "project": [],
        "communication": [],
        "ai": [],
        "todo": [],
        "reusable": [],
    }

    for record in records:
        if _is_generated_report_prompt(record):
            continue
        is_project = _is_project_progress(record)
        is_communication = _is_meeting_or_communication(record)
        is_ai = _is_ai_collaboration(record)
        is_todo = _is_todo(record)
        is_reusable = _is_reusable_content(record)

        if is_communication:
            buckets["communication"].append(record)
        if is_project and record.source != "feishu":
            buckets["project"].append(record)

        if is_ai:
            buckets["ai"].append(record)

        if is_todo:
            buckets["todo"].append(record)

        if is_reusable:
            buckets["reusable"].append(record)

    return buckets


def _contains_any(text: str, keywords: Iterable[str]) -> bool:
    normalized = text.lower()
    return any(keyword.lower() in normalized for keyword in keywords)


def _is_generated_report_prompt(record: SourceRecord) -> bool:
    text = " ".join([record.title, record.content])
    return "请基于下面的原始记录，写一份结构化中文工作" in text or "请基于下面的记录摘要，写一份结构化中文工作" in text or (
        "输出格式 请严格使用以下六个二级标题" in text and "压缩记录摘要" in text
    ) or (
        "输出格式 请严格使用以下二级标题" in text and "压缩记录摘要" in text
    )


def _overall_summary(period_name: str, records: Sequence[SourceRecord]) -> str:
    if not records:
        return f"- {period_name}暂无可归纳的工作记录，需人工补充。（来源: 采集状态 / 原始记录）"
    sources = sorted({record.source_label() for record in records})
    return f"- {period_name}共汇总 {len(records)} 条记录，覆盖 {', '.join(sources)}，请优先核对待确认事项。（来源: 采集状态 / 原始记录）"


def _daily_summary_paragraph(
    records: Sequence[SourceRecord],
    buckets: dict[str, list[SourceRecord]],
    collection_status: dict[str, str],
) -> str:
    if not records:
        return "今天暂无可归纳的工作记录，需人工补充。（来源: 采集状态 / 原始记录）"

    project_groups = _group_records(buckets["project"], _project_key, limit=3)
    communication_groups = _group_records(buckets["communication"], _communication_key, limit=2)
    ai_groups = _group_records(buckets["ai"], _ai_key, limit=3)
    risk_records = [record for record in buckets["todo"] if _is_risk(record)]
    sources = sorted({record.source_label() for record in records})
    risk_text = (
        f"需优先确认 {_joined_titles(risk_records, limit=3)}"
        if risk_records
        else "未识别明确阻塞，普通后续事项已收敛到后续事项"
    )
    return (
        f"今天主要推进 {_group_names(project_groups) or '若干工作主题'}，工作类型覆盖 "
        f"{_work_type_summary(buckets['project'])}；AI 协作集中在 "
        f"{_group_names(ai_groups) or '任务处理'}，沟通会议重点涉及 "
        f"{_group_names(communication_groups) or '暂无明确沟通主题'}。{risk_text}。"
        f"数据覆盖 {len(records)} 条记录，来源包括 {', '.join(sources)}；"
        f"{_collection_status_summary(collection_status)}。（来源: 采集状态 / 原始记录）"
    )


def _weekly_summary_paragraph(
    records: Sequence[SourceRecord],
    buckets: dict[str, list[SourceRecord]],
    collection_status: dict[str, str],
) -> str:
    if not records:
        return "本周暂无可归纳的工作记录，需人工补充项目结果、问题复盘和下周计划。（来源: 采集状态 / 原始记录）"

    project_groups = _group_records(buckets["project"], _project_key, limit=3)
    communication_groups = _group_records(buckets["communication"], _communication_key, limit=2)
    todo_groups = _weekly_todo_groups(buckets["todo"], limit=3)
    risk_records = [record for record in buckets["todo"] if _is_risk(record)]
    sources = sorted({record.source_label() for record in records})
    return (
        f"本周工作重心集中在 {_group_names(project_groups) or '若干工作主题'}，阶段性产出覆盖 "
        f"{_work_type_summary(buckets['project'])}；AI/自动化主要支撑 "
        f"{_work_type_summary(buckets['ai'])}，会议沟通围绕 "
        f"{_group_names(communication_groups) or '暂无明确沟通主题'} 展开。"
        f"问题复盘上，{_weekly_risk_summary(risk_records)}；下周优先推进 "
        f"{_group_names(todo_groups, limit=4) or '团队确认后的重点事项'}。"
        f"本周共汇总 {len(records)} 条记录，来源包括 {', '.join(sources)}；"
        f"{_collection_status_summary(collection_status)}。（来源: 采集状态 / 原始记录）"
    )


def _project_sections(records: Sequence[SourceRecord], is_weekly: bool = False) -> list[str]:
    if not records:
        return ["暂无主要项目进展。（来源: 原始记录）"]

    lines: list[str] = []
    for key, group in _group_records(records, _project_key, limit=8):
        title = _brief_text(key, 90)
        next_step = "需先处理待确认/阻塞项" if any(_is_risk(record) for record in group) else _group_follow_up(group)
        lines.append(f"### {title}")
        if is_weekly:
            lines.extend(
                [
                    f"- 本周结果：形成/推进 {_joined_titles(group, limit=4)}。",
                    f"- 阶段产出：{_work_type_summary(group)}；当前状态：{_group_status(group)}。",
                    f"- 问题复盘/下周计划：{next_step}。（来源: {_joined_sources(group)}）",
                ]
            )
        else:
            lines.extend(
                [
                    f"- 工作目标：围绕 {title} 推进 {_work_type_summary(group)}。",
                    f"- 关键动作：{_joined_titles(group, limit=4)}。",
                    f"- 当前进展：{_group_status(group)}；下一步：{next_step}。（来源: {_joined_sources(group)}）",
                ]
            )
    return lines


def _ai_summary_lines(records: Sequence[SourceRecord], is_weekly: bool = False) -> list[str]:
    if not records:
        return ["- 暂无 AI 工具协作记录。（来源: 原始记录）"]

    lines: list[str] = []
    for key, group in _group_records(records, _ai_key, limit=6):
        follow_up = "相关风险已归入后续事项" if any(_is_risk(record) for record in group) else _group_follow_up(group)
        tool = _tool_name(key)
        if is_weekly:
            lines.append(
                f"- **{tool}**：本周主要用于 {_work_type_summary(group)}，支撑 {len(group)} 条记录处理；"
                f"下周需确认/跟进：{follow_up}。（来源: {_joined_sources(group)}）"
            )
        else:
            lines.append(
                f"- **{tool}**：今天主要用于 {_work_type_summary(group)}，支撑 {len(group)} 条 AI/自动化记录处理；"
                f"人工确认/跟进：{follow_up}。（来源: {_joined_sources(group)}）"
            )
    return lines


def _communication_summary_lines(records: Sequence[SourceRecord], is_weekly: bool = False) -> list[str]:
    if not records:
        return ["暂无会议或沟通结论。（来源: 原始记录）"]

    groups = _group_records(records, _communication_key, limit=4)
    period_name = "本周" if is_weekly else "今天"
    lines = [
        (
            f"{period_name}共沉淀 {len(records)} 条会议/沟通记录，重点涉及 "
            f"{_group_names(groups, limit=4) or '暂无明确沟通主题'}。（来源: {_joined_sources(records)}）"
        )
    ]
    for key, group in groups[:3]:
        lines.append(
            f"- **{_brief_text(key, 90)}**：{_communication_result(group)}；"
            f"待跟进：{_group_follow_up(group)}。（来源: {_joined_sources(group)}）"
        )
    return lines


def _followup_lines(
    todo_records: Sequence[SourceRecord],
    reusable_records: Sequence[SourceRecord],
    is_weekly: bool = False,
) -> list[str]:
    lines: list[str] = []
    groups = _weekly_todo_groups(todo_records, limit=6) if is_weekly else _todo_groups(todo_records, limit=6)
    if groups:
        lines.extend(_followup_group_line(key, group, is_weekly=is_weekly) for key, group in groups)
        shown = sum(len(group) for _, group in groups)
        remaining = len(todo_records) - shown
        if remaining > 0:
            lines.append(f"- 另有 {remaining} 条低优先级待确认/后续记录保留在来源 bundle 中，可按需回查。（来源: 原始记录）")
    else:
        empty_text = "暂无明确下周计划，建议结合团队优先级补充" if is_weekly else "暂无明确后续事项，建议结合团队优先级补充"
        lines.append(f"- {empty_text}。（来源: 采集状态 / 原始记录）")

    reusable_groups = _group_records(reusable_records, _reusable_key, limit=3)
    if reusable_groups:
        lines.append(
            f"- 可沉淀：{_group_names(reusable_groups, limit=3)} 可整理为 "
            f"{_reusable_group_forms([record for _, group in reusable_groups for record in group])}。"
            f"（来源: {_group_sources(reusable_groups)}）"
        )
    return lines


def _followup_group_line(key: str, records: Sequence[SourceRecord], is_weekly: bool = False) -> str:
    has_risk = any(_is_risk(record) for record in records)
    lead = "需优先确认" if has_risk else ("下周继续推进" if is_weekly else "继续推进")
    if len(records) == 1:
        record = records[0]
        detail = _brief_text(record.content, 120) if has_risk else _group_follow_up(records)
        return (
            f"- {lead}：{_report_title(record, 90)}，{detail}{_risk_suffix(record)}。"
            f"（来源: {record.source_label()}）"
        )
    return (
        f"- {lead}：{_brief_text(key, 90)}，重点包括 {_joined_titles(records, limit=4)}；"
        f"建议：{_group_follow_up(records)}。（来源: {_joined_sources(records)}）"
    )


def _tool_name(source_label: str) -> str:
    return _brief_text(source_label.split(" / ", 1)[0], 60)


def _communication_result(records: Sequence[SourceRecord]) -> str:
    return "；".join(_unique((_communication_record_summary(record) for record in records), limit=2)) or "沟通记录已归档"


def _communication_record_summary(record: SourceRecord) -> str:
    title = _report_title(record, 60)
    if record.source == "feishu":
        if record.source_detail == "calendar":
            return f"{title}: 会议/日程记录已归档"
        if record.source_detail.startswith("drive"):
            return f"{title}: 文档更新记录已归档"
        content = _communication_content_topic(record.content)
        if content:
            return f"{title}: 围绕 {content} 沟通"
        return f"{title}: 飞书沟通记录已归档"
    return f"{title}: {_brief_text(record.content, 100)}"


def _communication_content_topic(text: str) -> str:
    cleaned = re.sub(r"会话消息\s*\d+\s*条；代表内容:\s*", "", text)
    cleaned = re.sub(r"\[Image:[^\]]+\]", "", cleaned)
    cleaned = re.sub(r"<file[^>]*>", "文件", cleaned)
    cleaned = re.sub(r"https?://\S+", "链接资料", cleaned)
    cleaned = re.sub(r"[@＠]\S+", "相关同事", cleaned)
    cleaned = re.sub(r"\boc_[0-9a-fA-F]{12,}\b", "飞书私聊沟通", cleaned)
    cleaned = re.sub(r"\b[A-Za-z0-9_-]{12,}\b", "", cleaned)
    cleaned = _drop_symbol_emoji(cleaned)
    normalized = cleaned.lower()
    if _contains_any(normalized, ("招聘", "面试", "软件岗位", "候选人")):
        return "软件岗位招聘与面试安排"
    if _contains_any(normalized, ("y轴", "x轴", "光栅尺", "丝杆", "物料", "安装测试")):
        return "设备轴向改善、物料安装测试与方案设计"
    if _contains_any(normalized, ("扫描件", "顺丰", "盖章", "寄出")):
        return "文件盖章、扫描件和邮寄确认"
    if _contains_any(normalized, ("aigc", "ai的开发", "ai 开发", "开发计划")):
        return "AIGC 与 AI 开发计划讨论"
    if _contains_any(normalized, ("工作日报总结", "工作日报", "补报")):
        return "日报同步与补报记录"
    if cleaned.strip() == "链接资料":
        return "外部资料同步"
    parts = [
        part.strip(" ；;，,。.")
        for part in re.split(r"[；;。\n]+", cleaned)
        if part.strip(" ；;，,。.")
    ]
    parts = [
        part
        for part in parts
        if part not in {"好的", "收到", "收悉", "ok", "OK"} and not part.startswith("[Image:")
    ]
    return _brief_text("；".join(parts[:2]), 100) if parts else ""


def _daily_overview_lines(
    records: Sequence[SourceRecord],
    buckets: dict[str, list[SourceRecord]],
    collection_status: dict[str, str],
) -> list[str]:
    if not records:
        return ["- 今天暂无可归纳的工作记录，需人工补充。（来源: 采集状态 / 原始记录）"]

    project_groups = _group_records(buckets["project"], _project_key, limit=3)
    communication_groups = _group_records(buckets["communication"], _communication_key, limit=2)
    risk_records = [record for record in buckets["todo"] if _is_risk(record)]
    sources = sorted({record.source_label() for record in records})

    return [
        (
            f"- 今日重点推进 {_group_names(project_groups) or '若干工作主题'}；"
            f"工作类型覆盖 {_work_type_summary(buckets['project'])}。（来源: {_group_sources(project_groups) or '原始记录'}）"
        ),
        (
            f"- AI/自动化协作处理 {len(buckets['ai'])} 条记录，重点支撑 {_group_names(_group_records(buckets['ai'], _ai_key, limit=3)) or '任务处理'}。"
            f"（来源: {_joined_sources(buckets['ai']) or '原始记录'}）"
        ),
        (
            f"- 会议/沟通沉淀 {len(buckets['communication'])} 条，关键主题为 {_group_names(communication_groups) or '暂无明确沟通主题'}。"
            f"（来源: {_group_sources(communication_groups) or '原始记录'}）"
        ),
        (
            f"- 风险与待办: {_weekly_risk_summary(risk_records) if risk_records else '未识别明确 P0 阻塞，普通后续事项已归入待办区'}。"
            f"（来源: {_joined_sources(risk_records) if risk_records else '原始记录'}）"
        ),
        (
            f"- 数据覆盖: 共汇总 {len(records)} 条记录，覆盖 {', '.join(sources)}；"
            f"{_collection_status_summary(collection_status)}（来源: 采集状态）"
        ),
    ]


def _weekly_overview_lines(
    records: Sequence[SourceRecord],
    buckets: dict[str, list[SourceRecord]],
    collection_status: dict[str, str],
) -> list[str]:
    if not records:
        return ["- 本周暂无可归纳的工作记录，需人工补充项目结果、问题复盘和下周计划。（来源: 采集状态 / 原始记录）"]

    project_groups = _group_records(buckets["project"], _project_key, limit=3)
    communication_groups = _group_records(buckets["communication"], _communication_key, limit=2)
    todo_groups = _weekly_todo_groups(buckets["todo"], limit=3)
    risk_records = [record for record in buckets["todo"] if _is_risk(record)]
    sources = sorted({record.source_label() for record in records})

    return [
        (
            f"- 本周重点聚焦 {_group_names(project_groups) or '若干工作主题'}；"
            f"共沉淀 {len(buckets['project'])} 条项目/交付记录，工作类型覆盖 {_work_type_summary(buckets['project'])}。"
            f"（来源: {_group_sources(project_groups) or '原始记录'}）"
        ),
        (
            f"- 阶段结果: AI/自动化协作覆盖 {len(buckets['ai'])} 条记录，支撑 {_group_names(_group_records(buckets['ai'], _ai_key, limit=3)) or '任务处理'}；"
            f"会议与沟通沉淀 {len(buckets['communication'])} 条，关键沟通主题为 {_group_names(communication_groups) or '暂无明确沟通主题'}。"
            f"（来源: {_joined_sources(buckets['ai'] + buckets['communication']) or '原始记录'}）"
        ),
        (
            f"- 问题复盘: {_weekly_risk_summary(risk_records)}"
            f"（来源: {_joined_sources(risk_records) if risk_records else '原始记录'}）"
        ),
        (
            f"- 下周重点: {_weekly_todo_summary(todo_groups)}"
            f"（来源: {_group_sources(todo_groups) or '原始记录'}）"
        ),
        (
            f"- 数据覆盖: 本周共汇总 {len(records)} 条记录，覆盖 {', '.join(sources)}；"
            f"{_collection_status_summary(collection_status)}（来源: 采集状态）"
        ),
    ]


def _record_bullets(records: Sequence[SourceRecord]) -> list[str]:
    if not records:
        return ["- 暂无。（来源: 原始记录）"]
    return [
        f"- {record.title}: {_brief_text(record.content)}（来源: {record.source_label()}{'，待确认' if record.needs_confirmation else ''}）"
        for record in records
    ]


def _project_bullets(records: Sequence[SourceRecord], is_weekly: bool = False) -> list[str]:
    if not records:
        return ["- 暂无重点项目进展。（来源: 原始记录）"]
    return [
        _project_group_bullet(key, group, is_weekly=is_weekly)
        for key, group in _group_records(records, _project_key, limit=12)
    ]


def _communication_bullets(records: Sequence[SourceRecord]) -> list[str]:
    if not records:
        return ["- 暂无会议或沟通结论。（来源: 原始记录）"]
    return [
        _communication_group_bullet(key, group)
        for key, group in _group_records(records, _communication_key, limit=8)
    ]


def _ai_bullets(records: Sequence[SourceRecord], is_weekly: bool = False) -> list[str]:
    if not records:
        return ["- 暂无 AI 工具协作记录。（来源: 原始记录）"]
    return [
        _ai_group_bullet(key, group, is_weekly=is_weekly)
        for key, group in _group_records(records, _ai_key, limit=12)
    ]


def _todo_bullets(records: Sequence[SourceRecord], is_weekly: bool = False) -> list[str]:
    if not records:
        return ["- P2 暂无明确待办，请结合团队优先级补充。（来源: 采集状态 / 原始记录）"]
    groups = _weekly_todo_groups(records, limit=8) if is_weekly else _todo_groups(records, limit=12)
    bullets = [_todo_group_bullet(key, group, is_weekly=is_weekly) for key, group in groups]
    shown = sum(len(group) for _, group in groups)
    remaining = len(records) - shown
    if remaining > 0:
        bullets.append(f"- P2 另有 {remaining} 条低优先级待确认/后续记录已保留在来源 bundle 中，可按需回查。（来源: 原始记录）")
    return bullets


def _reusable_bullets(records: Sequence[SourceRecord]) -> list[str]:
    if not records:
        return ["- 暂无明确可沉淀内容。（来源: 原始记录）"]
    groups = _group_records(records, _reusable_key, limit=8)
    bullets = [_reusable_group_bullet(key, group) for key, group in groups]
    shown = sum(len(group) for _, group in groups)
    remaining = len(records) - shown
    if remaining > 0:
        bullets.append(f"- 另有 {remaining} 条候选沉淀内容已保留在来源 bundle 中，建议后续按主题二次筛选。（来源: 原始记录）")
    return bullets


def _group_records(
    records: Sequence[SourceRecord],
    key_func: Callable[[SourceRecord], str],
    limit: int,
) -> list[tuple[str, list[SourceRecord]]]:
    grouped: dict[str, list[SourceRecord]] = {}
    for record in records:
        key = key_func(record)
        grouped.setdefault(key, []).append(record)

    def sort_key(item: tuple[str, list[SourceRecord]]) -> tuple[int, float, str]:
        key, group = item
        latest = max((record.timestamp.timestamp() for record in group if record.timestamp), default=0.0)
        return (len(group), latest, key)

    return sorted(grouped.items(), key=sort_key, reverse=True)[:limit]


def _project_key(record: SourceRecord) -> str:
    if record.project:
        return _friendly_project_name(record.project, record)
    if record.source == "feishu" and record.people:
        return "、".join(record.people[:3])
    if record.source == "feishu" and record.title.startswith("oc_"):
        return "飞书私聊沟通"
    return record.title


def _communication_key(record: SourceRecord) -> str:
    if record.people:
        return "、".join(record.people[:3])
    if record.title.startswith("oc_"):
        return "飞书私聊沟通"
    return record.title


def _ai_key(record: SourceRecord) -> str:
    return record.source_label()


def _todo_key(record: SourceRecord) -> str:
    if record.project:
        return _friendly_project_name(record.project, record)
    return record.title or record.source_label()


def _weekly_todo_key(record: SourceRecord) -> str:
    return _friendly_project_name(record.project, record) if record.project else record.title or record.source_label()


def _reusable_key(record: SourceRecord) -> str:
    return _topic_key(record) or _reusable_form(record)


def _project_group_bullet(key: str, records: Sequence[SourceRecord], is_weekly: bool = False) -> str:
    next_step = "详见待办事项中的 P0/P1 跟进" if any(_is_risk(record) for record in records) else _group_follow_up(records)
    if is_weekly:
        return (
            f"- {_brief_text(key, 90)}: 本周结果: 形成/推进 {_joined_titles(records, limit=4)}；"
            f"阶段产出: {_work_type_summary(records)}，共 {len(records)} 条记录；"
            f"问题复盘: {_group_status(records)}；"
            f"下周计划: {next_step}（来源: {_joined_sources(records)}）"
        )
    return (
        f"- {_brief_text(key, 90)}: 汇总 {len(records)} 条相关记录；"
        f"关键动作/产出: {_joined_titles(records, limit=5)}；"
        f"当前状态: {_group_status(records)}；"
        f"下一步: {next_step}（来源: {_joined_sources(records)}）"
    )


def _communication_group_bullet(key: str, records: Sequence[SourceRecord]) -> str:
    if len(records) == 1:
        record = records[0]
        return (
            f"- {_brief_text(key, 90)}: 关键讨论/结论: {_joined_snippets(records, limit=1, content_limit=100)}；"
            f"待跟进: {_group_follow_up(records)}（来源: {record.source_label()}）"
        )
    return (
        f"- {_brief_text(key, 90)}: 汇总 {len(records)} 条会议/沟通记录；"
        f"关键讨论/结论: {_joined_snippets(records, limit=3, content_limit=100)}；"
        f"待跟进: {_group_follow_up(records)}（来源: {_joined_sources(records)}）"
    )


def _ai_group_bullet(key: str, records: Sequence[SourceRecord], is_weekly: bool = False) -> str:
    follow_up = "风险项已归入待办事项" if any(_is_risk(record) for record in records) else _group_follow_up(records)
    if is_weekly:
        return (
            f"- 工具/主题: {_brief_text(key, 90)}；本周协作价值: 支撑 {len(records)} 条记录处理，"
            f"主要用于 {_work_type_summary(records)}；"
            f"下周需确认/跟进: {follow_up}（来源: {_joined_sources(records)}）"
        )
    return (
        f"- 工具/主题: {_brief_text(key, 90)}；处理 {len(records)} 条 AI 协作记录；"
        f"协作类型: {_work_type_summary(records)}；"
        f"人工确认/跟进: {follow_up}（来源: {_joined_sources(records)}）"
    )


def _todo_group_bullet(key: str, records: Sequence[SourceRecord], is_weekly: bool = False) -> str:
    risk_count = sum(1 for record in records if _is_risk(record))
    follow_count = len(records) - risk_count
    priority = "P0" if risk_count else "P1"
    prefix = "下周重点 - " if is_weekly else ""
    if len(records) == 1:
        record = records[0]
        if not risk_count:
            return (
                f"- {priority} {prefix}{_report_title(record, 90)}: 需继续推进并明确下一步、责任人或完成时间。"
                f"（来源: {record.source_label()}）"
            )
        return (
            f"- {priority} {prefix}{_report_title(record, 90)}: {_brief_text(record.content, 120)}"
            f"{_risk_suffix(record)}（来源: {record.source_label()}）"
        )
    mix_summary = f"（含 {risk_count} 个 P0 风险、{follow_count} 个 P1 跟进）" if is_weekly and risk_count else ""
    return (
        f"- {priority} {prefix}{_brief_text(key, 90)}: 汇总 {len(records)} 条待跟进记录{mix_summary}；"
        f"重点事项: {_joined_titles(records, limit=5)}；"
        f"建议: {_group_follow_up(records)}（来源: {_joined_sources(records)}）"
    )


def _reusable_group_bullet(key: str, records: Sequence[SourceRecord]) -> str:
    if len(records) == 1:
        record = records[0]
        return (
            f"- {_report_title(record, 90)}: 可沉淀为{_reusable_form(record)}；"
            f"依据: {_brief_text(record.content, 120)}{_risk_suffix(record)}（来源: {record.source_label()}）"
        )
    return (
        f"- {_brief_text(key, 90)}: 可沉淀为{_reusable_group_forms(records)}；"
        f"依据: 汇总 {len(records)} 条候选记录，覆盖 {_work_type_summary(records)}"
        f"（来源: {_joined_sources(records)}）"
    )


def _joined_sources(records: Sequence[SourceRecord]) -> str:
    return "；".join(_unique((record.source_label() for record in records), limit=4))


def _joined_titles(records: Sequence[SourceRecord], limit: int) -> str:
    return "；".join(_unique((_report_title(record, 80) for record in records), limit=limit)) or "暂无"


def _joined_snippets(records: Sequence[SourceRecord], limit: int, content_limit: int = 140) -> str:
    snippets = []
    for record in records:
        title = _report_title(record, 60)
        content = _brief_text(record.content, content_limit)
        snippets.append(f"{title}: {content}")
    return "；".join(_unique(snippets, limit=limit)) or "暂无"


def _group_names(groups: Sequence[tuple[str, list[SourceRecord]]], limit: int = 3) -> str:
    names: list[str] = []
    for key, _ in groups:
        brief = _brief_text(key, 50)
        names.extend(part.strip() for part in brief.split("、") if part.strip())
    return "、".join(_unique(names, limit=limit))


def _group_titles(groups: Sequence[tuple[str, list[SourceRecord]]], limit: int = 4) -> str:
    records = [record for _, group in groups for record in group]
    return _joined_titles(records, limit=limit)


def _group_sources(groups: Sequence[tuple[str, list[SourceRecord]]], limit: int = 4) -> str:
    records = [record for _, group in groups for record in group]
    return "；".join(_unique((record.source_label() for record in records), limit=limit))


def _work_type_summary(records: Sequence[SourceRecord]) -> str:
    labels = [_work_type_label(record) for record in records]
    return "、".join(_unique(labels, limit=4)) or "综合推进"


def _work_type_label(record: SourceRecord) -> str:
    text = " ".join([record.title, record.content, " ".join(record.tags), record.project]).lower()
    if record.source == "browser":
        return "资料调研"
    if _contains_any(text, ("会议", "沟通", "对齐", "评审")) or record.people:
        return "项目沟通"
    if _contains_any(text, ("竞品", "调研", "资料", "eufymake", "creality", "bambu", "slicer")):
        return "资料调研"
    if _contains_any(text, ("需求", "prd", "产品", "方案", "体验", "叙事")):
        return "产品方案"
    if _contains_any(text, ("review", "compliance", "测试", "pytest", "qa", "验证", "复核")):
        return "测试验证"
    if _contains_any(text, ("修复", "fix", "实现", "开发", "workflow", "cli", "模型", "架构", "代码")):
        return "技术实现"
    if _contains_any(text, ("失败", "阻塞", "报错", "安装", "权限", "网络")):
        return "问题排查"
    if _contains_any(text, ("文档", "总结", "沉淀", "指南")):
        return "文档沉淀"
    return "综合推进"


def _weekly_risk_summary(records: Sequence[SourceRecord]) -> str:
    if not records:
        return "未识别明确阻塞，主要风险集中在普通待确认和采集完整性核对"
    return f"识别 {len(records)} 个需优先处理的风险/阻塞，重点包括 {_joined_titles(records, limit=3)}；需明确责任人和时间点"


def _weekly_todo_summary(groups: Sequence[tuple[str, list[SourceRecord]]]) -> str:
    if not groups:
        return "暂无明确下周待办，建议结合团队优先级补充"
    risk_count = sum(1 for _, group in groups if any(_is_risk(record) for record in group))
    follow_count = len(groups) - risk_count
    return f"优先推进 {_group_names(groups, limit=4)}；其中需优先确认 {risk_count} 项，常规项目跟进 {follow_count} 项"


def _collection_status_summary(collection_status: dict[str, str]) -> str:
    if not collection_status:
        return "暂无采集状态，需要人工确认数据完整性"
    problem_statuses = [
        f"{source}: {status}"
        for source, status in sorted(collection_status.items())
        if _contains_any(status, ("partial", "missing", "failed", "error", "失败", "缺失"))
    ]
    if problem_statuses:
        return "需关注采集状态 " + "；".join(problem_statuses[:3])
    return "采集状态未发现明显异常"


def _unique(values: Iterable[str], limit: int) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
        if len(result) >= limit:
            break
    return result


def _group_status(records: Sequence[SourceRecord]) -> str:
    return "存在待确认/阻塞信息，需继续核对" if any(_is_risk(record) for record in records) else "已有阶段性进展"


def _group_follow_up(records: Sequence[SourceRecord]) -> str:
    if any(_is_risk(record) for record in records):
        return "需确认风险项并明确责任人/时间点"
    if any(_is_todo(record) for record in records):
        return "按记录中的后续动作继续推进"
    return "暂无明确待办"


def _brief_text(text: str, limit: int = 260) -> str:
    brief = " ".join(text.split())
    brief = re.sub(r"\[([^\]]{1,80})\]\((?:plugin://|/Users/)[^)]+\)", r"\1", brief)
    brief = re.sub(r"\[Image:[^\]]+\]", "图片", brief)
    brief = re.sub(r"<file[^>]*>", "文件", brief)
    brief = re.sub(r"https?://\S+", "链接资料", brief)
    brief = re.sub(r"\boc_[0-9a-fA-F]{12,}\b", "飞书私聊沟通", brief)
    brief = re.sub(r"\bimg_v3_[\w-]+\b", "图片", brief)
    brief = re.sub(r"\bfile_v3_[\w-]+\b", "文件", brief)
    brief = _drop_symbol_emoji(brief)
    brief = (
        brief.replace("```", "'''")
        .replace("[$superpowers:", "[＄superpowers:")
        .replace("$superpowers:", "＄superpowers:")
        .replace("##", "＃＃")
    )
    if len(brief) <= limit:
        return brief
    return brief[: limit - 3].rstrip() + "..."


def _drop_symbol_emoji(text: str) -> str:
    return "".join(char for char in text if not (0x1F000 <= ord(char) <= 0x1FAFF))


def _report_title(record: SourceRecord, limit: int = 80) -> str:
    raw = " ".join([record.title, record.project]).lower()
    full_text = " ".join([record.title, record.content, record.project]).lower()
    title = record.title.lower()
    if title.startswith("oc_"):
        return "飞书私聊沟通"
    if title.startswith("http://") or title.startswith("https://"):
        return "链接资料"
    if title.startswith("fix lark-cli update network timeout"):
        return "修复 lark-cli 更新超时问题"
    if title.startswith("review docs spec"):
        return "飞书文档写入规范复核"
    if title.startswith("add local collectors"):
        return "补齐本地记录采集器"
    if "doubaoimeinstaller" in title or "已损坏，无法打开" in title:
        return "豆包输入法安装问题排查"
    if _contains_any(full_text, ("daily-report", "work_report", "日报", "周报", "summarizer", "lark_writer", "workflow.py", "fallback report", "review task")):
        if title.startswith("add config"):
            return "补齐配置与 CLI 参数"
        if title.startswith("add local collectors"):
            return "补齐本地记录采集器"
        if title.startswith("fix lark-cli update network timeout"):
            return "修复 lark-cli 更新超时问题"
        if title.startswith("review docs spec"):
            return "飞书文档写入规范复核"
        if title.startswith("fix daily report headings"):
            return "修正日报/周报标题结构"
        if title.startswith("fix gate review findings"):
            return "修复评审发现的问题"
        if title.startswith("review") or "compliance" in title or "quality review" in title:
            return "日报工作流规范/实现复核"
        if "package skeleton" in title or "models" in title:
            return "搭建日报工作流包结构与数据模型"
        if "workflow orchestration" in title:
            return "补齐日报生成与飞书写入编排"
        if "fallback" in title or "summarizer" in title:
            return "优化日报/周报总结模板"
        if "lark" in title or "feishu" in title:
            return "优化飞书写入与通知链路"
    if "brainstorming-users-dt-agents-skills-brainstorming" in raw:
        if "竞品" in record.title or "切片" in record.title:
            return "切片软件竞品分析方案"
        if "typeless" in raw:
            return "Typeless 登录状态修复"
        return "AI 方案脑暴"
    if "superpowers:brainstorming" in raw:
        return "AI 方案脑暴"
    if "plugin://computer-use" in raw or "[@电脑]" in record.title:
        if "vmware" in raw:
            return "VMware 注册/下载协助"
        return "Computer Use 操作协助"
    return _brief_text(record.title, limit)


def _friendly_project_name(project: str, record: SourceRecord) -> str:
    normalized = project.strip().lower()
    record_text = " ".join([record.title, record.content, " ".join(record.tags)]).lower()
    if normalized in {"riemann_prd", "riemann-prd"}:
        return "Riemann 产品需求文档"
    if "brainstorming-users-dt-agents-skills-brainstorming" in normalized:
        return "AI 方案脑暴与竞品分析"
    if normalized == "lmslicer":
        return "切片软件 / VMware 支持" if "vmware" in record_text else "切片软件"
    if normalized == "codex":
        if _contains_any(record_text, ("daily-report", "work_report", "日报", "周报", "summarizer", "lark_writer", "workflow.py", "review task", "spec compliance", "fallback report", "local collectors")):
            return "日报/周报自动化工作流"
        if _contains_any(record_text, ("切片", "竞品", "eufymake", "creality", "bambu", "orca", "slicer")):
            return "切片软件体验与竞品分析"
        if _contains_any(record_text, ("windows", "虚拟机", "vmware", "安装错误", "cc-switch", "doubao")):
            return "本地工具与环境问题"
        return "Codex 本地工具与自动化"
    return project


def _topic_key(record: SourceRecord) -> str:
    if record.project:
        return _friendly_project_name(record.project, record)
    text = " ".join([record.title, record.content, " ".join(record.tags)]).lower()
    if _contains_any(text, ("daily-report", "work_report", "日报", "周报", "summarizer", "lark_writer", "workflow.py", "review task", "fallback report", "local collectors", "dry-run")):
        return "日报/周报自动化工作流"
    if _contains_any(text, ("切片", "竞品", "eufymake", "creality", "bambu", "slicer")):
        return "切片软件体验与竞品分析"
    if _contains_any(text, ("riemann", "官网", "prd", "uv3d", "3d竞品")):
        return "Riemann 产品需求文档"
    return ""


def _risk_suffix(record: SourceRecord) -> str:
    return "；需确认" if _is_risk(record) else ""


def _people_suffix(record: SourceRecord) -> str:
    return f"；相关人: {', '.join(record.people)}" if record.people else ""


def _todo_groups(records: Sequence[SourceRecord], limit: int) -> list[tuple[str, list[SourceRecord]]]:
    grouped: dict[str, list[SourceRecord]] = {}
    for record in records:
        grouped.setdefault(_todo_key(record), []).append(record)

    def sort_key(item: tuple[str, list[SourceRecord]]) -> tuple[int, int, float, str]:
        key, group = item
        latest = max((record.timestamp.timestamp() for record in group if record.timestamp), default=0.0)
        return (0 if any(_is_risk(record) for record in group) else 1, -len(group), -latest, key)

    return sorted(grouped.items(), key=sort_key)[:limit]


def _weekly_todo_groups(records: Sequence[SourceRecord], limit: int) -> list[tuple[str, list[SourceRecord]]]:
    grouped: dict[str, list[SourceRecord]] = {}
    for record in records:
        grouped.setdefault(_weekly_todo_key(record), []).append(record)

    def sort_key(item: tuple[str, list[SourceRecord]]) -> tuple[int, int, float, str]:
        key, group = item
        latest = max((record.timestamp.timestamp() for record in group if record.timestamp), default=0.0)
        return (0 if any(_is_risk(record) for record in group) else 1, -len(group), -latest, key)

    return sorted(grouped.items(), key=sort_key)[:limit]


def _reusable_group_forms(records: Sequence[SourceRecord]) -> str:
    return "、".join(_unique((_reusable_form(record) for record in records), limit=3)) or "知识库条目"


def _reusable_form(record: SourceRecord) -> str:
    text = " ".join([record.title, record.content, " ".join(record.tags)])
    if _contains_any(text, ("产品", "需求")):
        return "产品文档"
    if _contains_any(text, ("技术", "开发", "实现", "架构")):
        return "技术文档"
    if _contains_any(text, ("流程", "规范", "SOP")):
        return "流程规范"
    if _contains_any(text, ("复盘", "问题", "失败", "阻塞")):
        return "复盘材料"
    return "知识库条目"
