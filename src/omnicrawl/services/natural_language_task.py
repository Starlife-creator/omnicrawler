"""自然语言任务编译器。

两层实现：
1. 确定性本地解析 — 始终可用，处理常见中文需求
2. AI 任务设计助手 — 当 AI provider 配置可用时，提供完整的智能分析和追问

安全保证：
- AI 不可用时仍可使用本地解析创建任务
- AI 不会自行扩大域名、关闭安全策略或写入凭据
- 所有 AI 输出经过 Schema 校验和安全性检查
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

from .ux_service import QuickTaskDraft, draft_quick_task

_URL = re.compile(r"https?://[^\s，。；;]+", re.IGNORECASE)
_FILE_EXT = re.compile(r"(?:^|\s)([\w\-/\\]+\.(?:pdf|docx?|xlsx?|pptx?|txt|csv|扫描件))", re.IGNORECASE)
_FILE_PATH = re.compile(r"([A-Za-z]:[/\\][^\s，。；;]+|[~/][^\s，。；;]+)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class NaturalLanguageDraft:
    request: str
    task: QuickTaskDraft
    topics: tuple[str, ...]
    schedule: str
    mode: str = "crawl"  # "crawl" | "pdf" | "ambiguous"
    safety_constraints: tuple[str, ...] = (
        "不扩大入口域名", "不写入真实凭据", "不关闭网络安全策略", "不跳过试跑和用户确认"
    )
    file_paths: tuple[str, ...] = ()  # 检测到的文件路径
    ai_enhanced: bool = False
    ai_assumptions: tuple[dict[str, str], ...] = ()
    ai_questions: tuple[dict[str, Any], ...] = ()
    ai_risks: tuple[dict[str, str], ...] = ()
    ai_recommendations: tuple[str, ...] = ()


def compile_natural_language(request: str, *, fallback_url: str = "") -> NaturalLanguageDraft:
    """三层判定解析自然语言需求。

    ① URL 匹配 → 爬虫模式
    ② 文件路径/扩展名匹配 → PDF 模式
    ③ 都没命中 → 返回 ambiguous 模式，由调用方弹出二选一对话框
    """
    match = _URL.search(request)
    url = match.group(0) if match else fallback_url.strip()

    # Layer 2: 检测文件路径
    file_paths: list[str] = []
    for m in _FILE_EXT.finditer(request):
        file_paths.append(m.group(1))
    for m in _FILE_PATH.finditer(request):
        fp = m.group(1)
        if fp not in file_paths and not _URL.match(fp):
            file_paths.append(fp)

    has_file = bool(file_paths)
    has_url = bool(url)

    # Layer 3: 都没命中 → ambiguous，由调用方处理
    if not has_url and not has_file:
        # 尝试用 fallback_url 作为保底
        if fallback_url.strip():
            url = fallback_url.strip()
        else:
            return NaturalLanguageDraft(
                request=request.strip(),
                task=draft_quick_task("file:///placeholder", "save_page"),
                topics=(),
                schedule="manual",
                mode="ambiguous",
            )

    if has_file and not has_url:
        # 纯文件模式 → PDF
        lowered = request.lower()
        schedule = "manual"
        task = draft_quick_task("file:///placeholder", "download_files")
        decisions = list(task.decisions)
        decisions.append("检测到文件路径，切换为 PDF 处理模式")
        task = replace(task, download_files=True, process_pdf=True, decisions=tuple(decisions))
        quoted = [
            next(value for value in match if value)
            for match in re.findall(r'[「「"『]([^」」"』]+)[」」"』]', request)
        ]
        return NaturalLanguageDraft(
            request=request.strip(),
            task=task,
            topics=tuple(quoted),
            schedule=schedule,
            mode="pdf",
            file_paths=tuple(file_paths),
        )

    # URL 模式 → 爬虫（现有逻辑）
    lowered = request.lower()
    wants_monitor = any(word in lowered for word in ("每周", "每天", "监测", "变化", "更新", "调度", "定期"))
    wants_download = any(word in lowered for word in ("附件", "pdf", "下载", ".doc", "表格"))
    wants_section = any(word in lowered for word in ("栏目", "列表", "全部", "所有", "整个", "翻页", "分页"))
    if wants_section:
        intent = "collect_section"
    elif wants_monitor:
        intent = "monitor_changes"
    elif wants_download:
        intent = "download_files"
    elif any(word in lowered for word in ("保存", "截图", "快照")):
        intent = "save_page"
    else:
        intent = "save_page"

    task = draft_quick_task(url, intent)
    decisions = list(task.decisions)
    if wants_download and not task.download_files:
        decisions.append("需求包含附件或 PDF，因此启用文件下载和 PDF 处理")
        task = replace(task, download_files=True, process_pdf=True, decisions=tuple(decisions))
    if wants_monitor and not task.monitor_changes:
        decisions.append("需求包含周期或变化监测，因此保留同址内容版本")
        task = replace(task, monitor_changes=True, decisions=tuple(decisions))
    quoted = [
        next(value for value in match if value)
        for match in re.findall(r'\u201c([^\u201d]+)\u201d|"([^"]+)"|\u300c([^\u300d]+)\u300d|\u300e([^\u300f]+)\u300e', request)
    ]

    if "每周" in request:
        schedule = "weekly"
    elif "每天" in request:
        schedule = "daily"
    elif "每月" in request:
        schedule = "monthly"
    elif any(word in request for word in ("监测", "调度", "定期")):
        schedule = "weekly"
    else:
        schedule = "manual"

    return NaturalLanguageDraft(
        request=request.strip(), task=task, topics=tuple(quoted), schedule=schedule, mode="crawl"
    )


def compile_with_ai(
    request: str,
    provider: Any,  # OpenAICompatibleProvider or similar
    *,
    available_components: str = "",
    mode: str = "simple",
) -> NaturalLanguageDraft | None:
    """使用 AI provider 增强自然语言解析。

    返回 None 表示 AI 不可用或返回无效结果；此时应回退到 compile_natural_language。

    Raises:
        RuntimeError: AI 调用失败（网络、超时、额度等）。
        ValueError: AI 返回结果 Schema 校验不通过。
    """
    from .ai_task_designer import (
        build_task_design_messages,
        parse_ai_task_output,
        validate_task_config_safety,
    )

    if not request.strip():
        raise ValueError("需求描述不能为空")

    messages = build_task_design_messages(request, available_components, mode)

    try:
        result = provider.generate(messages, temperature=0.0)
    except Exception as exc:
        raise RuntimeError(f"AI 调用失败：{exc}") from exc

    draft = parse_ai_task_output(result.text)

    # 安全性校验
    violations = validate_task_config_safety(draft.config_patch)
    if violations:
        raise ValueError("AI 配置违反安全边界：\n" + "\n".join(f"  - {v}" for v in violations))

    # 将 AI 输出转换为本地草稿格式
    known = draft.known_requirements
    url = str(known.get("url", ""))
    intent = str(known.get("task_intent", "save_page"))
    if not url:
        match = _URL.search(request)
        url = match.group(0) if match else ""

    task = draft_quick_task(url, intent)

    return NaturalLanguageDraft(
        request=request.strip(),
        task=task,
        topics=tuple(known.get("topics", []) if isinstance(known.get("topics"), list) else [known.get("topics", "")]),
        schedule=str(known.get("schedule", "manual")),
        ai_enhanced=True,
        ai_assumptions=tuple(
            {"field": a.get("field", ""), "value": str(a.get("value", "")),
             "reason": a.get("reason", ""), "confidence": a.get("confidence", "low")}
            for a in draft.assumptions
        ),
        ai_questions=tuple(draft.unresolved_questions),
        ai_risks=tuple(
            {"risk": r.get("risk", ""), "severity": r.get("severity", "medium"),
             "mitigation": r.get("mitigation", "")}
            for r in draft.risks
        ),
        ai_recommendations=tuple(draft.recommended_actions),
    )
