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
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from .ux_service import QuickTaskDraft, draft_quick_task

_URL = re.compile(r"https?://[^\s，。；;]+", re.IGNORECASE)
_FILE_EXT = re.compile(r"(?:^|\s)([\w\-/\\]+\.(?:pdf|docx?|xlsx?|pptx?|txt|csv|扫描件))", re.IGNORECASE)
# 本地路径分支加 (?<![\w:]) 前置断言，避免把 ftp://host/x 里的 p://host/x 误当本地路径
_FILE_PATH = re.compile(r"((?<![\w:])[A-Za-z]:[/\\][^\s，。；;]+|[~/][^\s，。；;]+)", re.IGNORECASE)
# 统一的引号主题词提取（PDF 与爬虫分支共用，支持弯引号/直引号/中文书名号）
_QUOTED = re.compile(r"“([^”]+)”|\"([^\"]+)\"|「([^」]+)」|『([^』]+)』")

# ── 需求语义提取（走查 R3.3 / R4.1） ──────────────────────────────────────
#
# 背景（0.13.0 实测）：字段清单、「按价格从低到高排序」、「统计每个用户的帖子数」、
# 「点击年份 2015 等待 AJAX」在旧实现里**全部丢失，且 `warnings` 为空**。
# 下面三组提取器的取向是**宁可少取、不可取垃圾**（与项目既有的"歧义不猜"一致）：
# 拿不准的 token 直接不取，而不是把一个像句子的字符串塞进字段名。

#: 「抓取 / 采集 / 获取 …」之后通常跟字段列举
_FIELD_VERBS = ("抓取", "采集", "获取", "提取", "抽出", "导出")
#: 字段列举的终止标志（句读，或紧随其后的下一步动作）
_FIELD_LIST_END = re.compile(r"[。；;\n]|输出|导出|保存到|保存为|并|然后|接着|，按|,按")
#: 列举前的量词 / 归属前缀（"该标签下所有" / "全部 50 页书籍的" 之类）。
#: ★ 每个分支都必须含**具体 token**：写成"全可选"的正则（各部分都带 `?`）会永远匹配 ——
#: `finditer` 在空匹配之后会立刻扩成 1 个字符，`re.sub` 于是把字段名的首字吃掉
#: （实测 `标题` → `题`）。
_QUANT_PREFIX = re.compile(
    r"^(?:"
    r"(?:该|本|这)[^，。；：、]{0,10}?(?:下|中|里|内)(?:的)?(?:所有|全部|每个|各)?"
    r"|(?:所有|全部|每个|各)"
    r"|前\s*\d+\s*个?"
    r"|全部\s*\d+\s*页[^，。；：、]{0,6}?的"
    r")"
)
_FIELD_SPLIT = re.compile(r"[、,，]|和|以及|与")
#: 单个字段名的长度上限 —— 超过基本说明它是半句话，不是字段名
_FIELD_MAX_LEN = 6
#: 这些词出现在 token 里说明它是**处理描述**（"去除货币符号"/"转换为数字"），不是字段名
_FIELD_TOKEN_REJECT = (
    "去除", "转换", "清洗", "归一", "格式化", "去重", "排序", "统计", "分组", "归档", "下载",
)

#: 需求里的「后处理」意图 —— 这些**都不会自动发生**（R5.1 落地后把排序/分组/聚合移出）。
_POST_PROCESSING_RULES: tuple[tuple[str, str], ...] = (
    ("排序", r"排序|升序|降序|从低到高|从高到低|由低到高|由高到低"),
    ("分组", r"分组|归档|按[^，。；]{1,12}(?:分组|归类)"),
    ("聚合统计", r"统计|聚合|求和|合计|平均|均值|最高|最低|计数|次数|频率|分布"),
    ("交叉表", r"交叉表|透视表|分组统计表"),
    ("新旧对比", r"对比|差异|变价|变化列表"),
)
#: 需求里明确要求的交互 —— 同样不会自动发生（要人工录一次动作）。
#:
#: ★ 「滚动」**刻意不在此列**：R3.2 起自动路径已能产出 `scroll_bottom` 动作
#: （实测同一需求 10 → 100 条），把它写成"做不到"会是**假话**。
#: ★「点击(?!量|数|率|排行|榜)」的负向断言是为了 `点击量` 这类**字段名**不被误判成交互需求。
_INTERACTION_RULES: tuple[tuple[str, str], ...] = (
    ("点击", r"点击(?!量|数|率|排行|榜)|单击|点开|点选|点一下"),
    ("表单填写", r"填写|填入|提交表单|勾选|输入[^，。；]{0,8}(?:后|再|然后)|选择[^，。；]{0,8}(?:后|再|然后)"),
    ("iframe", r"iframe|内嵌框架|嵌套框架"),
)
#: 「不会自动发生」的每一项 → **去处分组**。分组决定提醒里给出的下一步动作。
#:
#: ★ 完整性由测试钉住（每个规则名都必须显式登记）：新加规则忘写去处会在 CI 里红，
#: 而不是让用户看到一句含糊的提醒。运行期取不到分组时落到 `_DEFAULT_GAP_GROUP`
#: —— **宁可提醒得泛一点，也不让一句需求把解析整条打断**。
_DEMAND_GAP_GROUPS: dict[str, str] = {
    "排序": "post",
    "分组": "post",
    "聚合统计": "post",
    "交叉表": "post",
    "新旧对比": "compare",
    "点击": "interaction",
    "表单填写": "interaction",
    "iframe": "interaction",
}
_DEFAULT_GAP_GROUP = "post"
#: 分组 → 提醒的引导语（后半句接"：项目、项目"）。★ 每句都必须给出**可执行的去处**，
#: 只说"做不到"等于没说（§4.5：提示要能引导下一步）。
_DEMAND_GAP_ADVICE: dict[str, str] = {
    "post": "以下后处理当前还没有对等能力，需要你在导出结果后自行处理",
    "compare": "以下对比不会自动产出结果，可用 `omnicrawler compare-runs` 对比两次运行",
    "interaction": "以下交互不会由自动路径生成，可用 `omnicrawler record-actions` 录制动作后再运行",
}
#: 输出格式：命中即按需求**收窄**（旧实现恒为 xlsx+csv+jsonl 三种）
_OUTPUT_FORMAT_RULES: tuple[tuple[str, str], ...] = (
    ("csv", r"\bcsv\b|逗号分隔|表格文件"),
    ("json", r"\bjson\b|\bjsonl\b"),
    ("xlsx", r"\bexcel\b|\bxlsx\b|工作表"),
)
#: 需求里写明的页数（"全部 50 页" / "翻完 300 页"）
_PAGE_COUNT = re.compile(r"(?:全部|所有|共|翻完|一共)?\s*(\d{1,4})\s*页")


def _extract_requested_fields(request: str) -> tuple[str, ...]:
    """从「抓取 A、B 和 C」这类列举里取字段清单。

    只接受**长度 ≤ ``_FIELD_MAX_LEN`` 且不含动作词**的 token；否则宁可不取
    （把「该标签下所有引文」这种半句话当字段名，比少一个字段更糟）。
    """
    for verb in _FIELD_VERBS:
        for match in re.finditer(re.escape(verb), request):
            window = _FIELD_LIST_END.split(request[match.end(): match.end() + 60])[0]
            if "的" in window:  # "全部 50 页书籍的标题、价格…" ⇒ 取"的"之后
                window = window[window.rfind("的") + 1:]
            picked: list[str] = []
            for part in _FIELD_SPLIT.split(window):
                token = _QUANT_PREFIX.sub("", part.strip()).strip(" 的了")
                if not token or len(token) > _FIELD_MAX_LEN:
                    continue
                if any(word in token for word in _FIELD_VERBS):
                    continue
                if any(word in token for word in _FIELD_TOKEN_REJECT):
                    continue
                picked.append(token)
            if len(picked) >= 2:  # 至少两项才算"列举"，单项多半是误匹配
                return tuple(dict.fromkeys(picked))
    return ()


def _extract_post_processing(request: str) -> tuple[str, ...]:
    return tuple(name for name, pattern in _POST_PROCESSING_RULES if re.search(pattern, request))


def _extract_interaction_demands(request: str) -> tuple[str, ...]:
    """需求里明确要求的交互（点击 / 表单 / iframe）—— 见 :data:`_INTERACTION_RULES` 的边界说明。"""
    return tuple(name for name, pattern in _INTERACTION_RULES if re.search(pattern, request))


def _extract_output_formats(request: str) -> tuple[str, ...]:
    return tuple(name for name, pattern in _OUTPUT_FORMAT_RULES if re.search(pattern, request, re.I))


def _extract_page_count(request: str) -> int | None:
    match = _PAGE_COUNT.search(request)
    return int(match.group(1)) if match else None


def _demand_gap_warnings(names: Sequence[str]) -> tuple[str, ...]:
    """把「不会自动发生」的需求按**去处**分组，写成可执行的一句话。

    分组缺失时归入 :data:`_DEFAULT_GAP_GROUP`（运行期不抛错）——「每个规则名都登记了去处」
    这件事由测试钉住，资格在 CI 里生效；这里只保证**一句需求不会打断整条解析**。
    """
    grouped: dict[str, list[str]] = {}
    for name in names:
        grouped.setdefault(_DEMAND_GAP_GROUPS.get(name, _DEFAULT_GAP_GROUP), []).append(name)
    return tuple(
        f"{_DEMAND_GAP_ADVICE[group]}：{'、'.join(grouped[group])}"
        for group in ("post", "compare", "interaction")
        if group in grouped
    )


def _apply_request_semantics(
    task: QuickTaskDraft, request: str, *, extra_decisions: Sequence[str] = ()
) -> QuickTaskDraft:
    """把需求里的语义接到 ``task`` 上，并**如实声明做不到的**（走查 R3.3 / R4.1）。

    这是确定性解析与 AI 增强解析**共用的一处实现** —— 两个入口各写一份必然漂移，而实测
    正是如此：旧实现里字段清单、「按价格从低到高排序」、「统计每个用户的帖子数」、
    「点击年份 2015 等待 AJAX」全部静默丢失且 ``warnings`` 为空。

    **不变量**：凡进入 ``unsupported`` 的名称，都能在 ``warnings`` 里找到对应去处。
    """
    decisions = [*task.decisions, *extra_decisions]
    warnings = list(task.warnings)

    fields = _extract_requested_fields(request)
    post_processing = _extract_post_processing(request)
    interactions = _extract_interaction_demands(request)
    requested_formats = _extract_output_formats(request)
    page_count = _extract_page_count(request)
    # 这两类在当前自动路径下都不会发生 ⇒ 一律如实进 unsupported，并各自给出去处。
    unsupported = (*post_processing, *interactions)

    if fields:
        decisions.append("已从需求中读出字段清单：" + "、".join(fields))
    if page_count and page_count != task.max_pages:
        # 既有决策行里写死了默认页数（"最多处理 30 页"）—— 页数被需求改写后必须同步，
        # 否则确认单自相矛盾（决策说 30、访问范围说 50）。
        default_phrase = f"最多处理 {task.max_pages} 页"
        decisions = [
            line.replace(default_phrase, f"最多处理 {page_count} 页") if default_phrase in line else line
            for line in decisions
        ]
        decisions.append(f"页数取自需求：{page_count} 页")
    if requested_formats:
        decisions.append("按需求收窄输出格式：" + "、".join(requested_formats))
    warnings.extend(_demand_gap_warnings(unsupported))

    return replace(
        task,
        max_pages=page_count or task.max_pages,
        output_formats=requested_formats or task.output_formats,
        fields=fields,
        post_processing=post_processing,
        unsupported=unsupported,
        decisions=tuple(decisions),
        warnings=tuple(warnings),
    )


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
    extra_decisions = []
    if wants_download and not task.download_files:
        extra_decisions.append("需求包含附件或 PDF，因此启用文件下载和 PDF 处理")
    if wants_monitor and not task.monitor_changes:
        extra_decisions.append("需求包含周期或变化监测，因此保留同址内容版本")
    if wants_download or wants_monitor:
        task = replace(
            task,
            download_files=task.download_files or wants_download,
            process_pdf=task.process_pdf or wants_download,
            monitor_changes=task.monitor_changes or wants_monitor,
        )
    # 走查 R3.3 / R4.1：字段清单 / 后处理 / 输出格式 / 页数接上，做不到的写进 warnings。
    task = _apply_request_semantics(task, request, extra_decisions=extra_decisions)
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

    # 走查 R4.1：AI 路径**共用同一处**需求语义接线。否则同一条需求"开不开 AI"会给出
    # 不同的字段 / 后处理 / 不支持清单 —— 旧实现是两条路径都静默丢弃，只接一处则会变成
    # **开了 AI 反而更差**，那比不接更坏。
    task = _apply_request_semantics(task, request)

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
