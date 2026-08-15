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
# 本地路径分支加 (?<![\w:]) 前置断言，避免把 ftp://host/x 里的 p://host/x 误当本地路径
_FILE_PATH = re.compile(r"((?<![\w:])[A-Za-z]:[/\\][^\s，。；;]+|[~/][^\s，。；;]+)", re.IGNORECASE)
# 统一的引号主题词提取（PDF 与爬虫分支共用，支持弯引号/直引号/中文书名号）
_QUOTED = re.compile(r"“([^”]+)”|\"([^\"]+)\"|「([^」]+)」|『([^』]+)』")


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
            next((group for group in match.groups() if group), "")
            for match in _QUOTED.finditer(request)
        ]
        return NaturalLanguageDraft(
            request=request.strip(),
            task=task,
            topics=tuple(item for item in quoted if item),
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
        next((group for group in match.groups() if group), "")
        for match in _QUOTED.finditer(request)
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
) -> NaturalLanguageDraft:
    """使用 AI provider 增强自然语言解析（E11：与 docstring 契约对齐）。

    本函数从不返回 None——provider 不可用时由调用方先行判断并回退到
    compile_natural_language（见 home.py 的 _AIEnrichWorker）。

    Raises:
        RuntimeError: AI 调用失败（网络、超时、额度等）。
        AIBudgetExceededError: AI 预算超限（RuntimeError 子类，不可重试）。
        AISafetyViolationError: AI 建议越过安全边界被拦截（ValueError 子类，C25）。
        ValueError: AI 返回结果 Schema 校验不通过。
    """
    from .ai_safety import AIBudgetExceededError, AISafetyViolationError
    from .ai_task_designer import (
        _append_ai_audit,
        ai_task_design_audit,
        build_task_design_messages,
        parse_ai_task_output,
        validate_task_config_safety,
    )

    if not request.strip():
        raise ValueError("需求描述不能为空")

    # B05-019：AI 任务设计输入（可能含页面摘录/URL）外发前过隐私闸门——
    # 未显式开启 allow_page_text 即拒发（fail-closed）。mock/无该方法 provider 跳过。
    check = getattr(provider, "check_content_allowed", None)
    if callable(check):
        check("allow_page_text", "AI 任务设计输入")

    messages = build_task_design_messages(request, available_components, mode)

    try:
        # C27：显式要求 JSON 对象输出，降低模型返回 Markdown 围栏的概率
        result = provider.generate(messages, temperature=0.0, response_format={"type": "json_object"})
    except AIBudgetExceededError as exc:
        # 预算超限不是瞬时故障，不应包装成可重试的"调用失败"；仍落审计
        _append_ai_audit({
            "provider": str(getattr(provider, "name", "unknown")),
            "model": str(getattr(provider, "model", "")),
            "status": "budget_exceeded",
            "error": str(exc)[:300],
        })
        raise
    except Exception as exc:
        _append_ai_audit({
            "provider": str(getattr(provider, "name", "unknown")),
            "model": str(getattr(provider, "model", "")),
            "status": "failed",
            "error": str(exc)[:300],
        })
        raise RuntimeError(f"AI 调用失败：{exc}") from exc

    draft = parse_ai_task_output(result.text, request=request)

    # 安全性校验（C30：按用户入口域名做包含校验，拒绝扩大域名）
    entry_domains = [match.group(0) for match in _URL.finditer(request)]
    violations = validate_task_config_safety(draft.config_patch, allowed_domains=entry_domains)
    if violations:
        _append_ai_audit({
            "provider": str(getattr(provider, "name", "unknown")),
            "model": str(getattr(provider, "model", "")),
            "status": "blocked",
            "violations": violations,
        })
        raise AISafetyViolationError(violations)

    # C32：审计落盘
    _append_ai_audit(ai_task_design_audit(result, draft))

    # 将 AI 输出转换为本地草稿格式
    known = draft.known_requirements
    url = str(known.get("url", ""))
    intent = str(known.get("intent") or known.get("task_intent") or "save_page")
    if not url:
        match = _URL.search(request)
        url = match.group(0) if match else ""

    try:
        task = draft_quick_task(url, intent)
    except ValueError:
        # AI 返回了非法 intent：先保留 url 降级为 save_page；
        # url 本身非法时再用占位地址，不整体作废
        try:
            task = draft_quick_task(url, "save_page")
        except ValueError:
            task = draft_quick_task("https://example.com/", "save_page")

    topics_raw = known.get("topics", [])
    topics_list = topics_raw if isinstance(topics_raw, list) else [topics_raw]
    topics = tuple(item for item in (str(t or "").strip() for t in topics_list) if item)

    return NaturalLanguageDraft(
        request=request.strip(),
        task=task,
        topics=topics,
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
