"""AI 任务设计助手。

将自然语言需求转换为结构化爬虫配置的完整流水线：

自然语言 → AI输出受约束JSON → JSON Schema校验 → 转换为CrawlConfig
→ 配置完整性校验 → 网络与权限校验 → 生成YAML和Task IR → 显示配置差异 → 用户确认

核心原则：
- AI不得自行扩大访问域名
- 不得关闭网络安全策略
- 不得把真实API Key写入配置
- 未经确认不得发送正文、PDF、截图、Cookie或凭据
- AI生成的分页和选择器必须经过实际探测
- 配置必须通过确定性校验才能执行
- AI不可用时仍可使用传统向导
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlsplit

from ..core.safe_data import safe_json_loads
from .ai_providers import _ESTIMATED_COST_PER_TOKEN, AIResult
from .ai_safety import ai_audit_record, mark_untrusted, validate_ai_output

# 审计 JSONL 写入互斥（_append_ai_audit 并发安全）
_AUDIT_LOCK = threading.Lock()

# ---------------------------------------------------------------------------
# 受约束的 AI 输出 Schema
# ---------------------------------------------------------------------------

AI_TASK_OUTPUT_SCHEMA: dict[str, type | tuple[type, ...]] = {
    "known_requirements": dict,       # 用户明确要求
    "assumptions": list,              # 系统假设
    "unresolved_questions": list,     # 待确认问题（每项含 question/why/options）
    "config_patch": dict,             # 建议的配置修改
    "explanations": list,             # 每项修改的原因（含 field/before/after/why）
    "risks": list,                    # 风险清单（含 risk/severity/mitigation）
    "recommended_actions": list,      # 建议的下一步操作
}

# ---------------------------------------------------------------------------
# AI 自然语言处理的 Prompt 模板
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """你是一个数据采集任务的设计助手。你的职责是将用户的自然语言需求转换为结构化的采集配置建议。

## 安全边界（绝对不可违反）

1. 不得扩大用户指定的域名范围
2. 不得建议关闭网络安全策略
3. 不得把任何 API Key、密码、Token 写入配置
4. 未经用户明确确认，不得建议发送网页正文、PDF 内容或截图给外部服务
5. 你必须标记所有系统假设，不能自行填满所有参数
6. 如果信息不完整，你必须主动列出待确认问题

## 输出格式

你必须输出严格的 JSON，包含以下字段：

{
  "known_requirements": {
    "url": "用户指定的网址",
    "intent": "save_page | collect_section | download_files | monitor_changes",
    "topics": ["主题词列表"],
    "schedule": "manual | daily | weekly | custom",
    "output_format": "用户要求的格式",
    "explicit_requirements": ["用户明确列出的所有要求"]
  },
  "assumptions": [
    {"field": "字段名", "value": "假设值", "reason": "原因", "confidence": "high | medium | low"},
    ...
  ],
  "unresolved_questions": [
    {"question": "问题描述", "why": "为什么需要确认", "options": ["选项1", "选项2"], "recommendation": "推荐选项"},
    ...
  ],
  "config_patch": {
    "seed_urls": ["..."],
    "task_intent": "...",
    "source_kind": "...",
    "max_pages": 0,
    "process_pdf": false,
    "monitor_same_url": false,
    "download_extensions": [".pdf"],
    "output_formats": ["jsonl"],
    "topic_filter": {"include_any": [], "exclude": []},
    "schedule": {"interval": "weekly", "day": "monday"}
  },
  "explanations": [
    {"field": "字段路径", "before": "原值", "after": "新值", "why": "修改原因"},
    ...
  ],
  "risks": [
    {"risk": "风险描述", "severity": "high | medium | low", "mitigation": "缓解措施"},
    ...
  ],
  "recommended_actions": [
    "建议用户执行的下一步操作",
    ...
  ]
}

## 规则

- 没有网址 → unresolved_questions 中要求用户提供
- 没有明确目标 → 询问用户想要什么结果
- PDF 处理 → 询问是否需要 OCR（扫描件）
- 监测 → 询问检测频率
- 永远不要猜测认证信息
- 任何高置信度(high)的假设都可以直接使用，但必须在 assumptions 中记录
- medium 和 low 的假设必须放入 unresolved_questions
"""

USER_PROMPT_TEMPLATE = """请分析以下数据采集需求，生成结构化配置建议。

用户需求：
{user_request}

已知上下文：
- 目标网站类型：未知（需探测）
- 可用组件：{available_components}
- 当前模式：{mode}

请严格按照输出格式返回 JSON 结果。如果有不确定的信息，请优先放入 unresolved_questions。"""

# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TaskDesignDraft:
    """AI 任务设计草稿。"""
    request: str
    known_requirements: dict[str, Any]
    assumptions: list[dict[str, Any]]
    unresolved_questions: list[dict[str, Any]]
    config_patch: dict[str, Any]
    explanations: list[dict[str, Any]]
    risks: list[dict[str, Any]]
    recommended_actions: list[str]
    safety_constraints: tuple[str, ...] = (
        "不扩大入口域名", "不写入真实凭据", "不关闭网络安全策略",
        "不跳过试跑和用户确认", "不发送敏感内容给外部AI",
    )

    @property
    def has_unresolved(self) -> bool:
        """是否还有未确认的问题。"""
        return len(self.unresolved_questions) > 0

    @property
    def has_risks(self) -> bool:
        """是否存在需要注意的风险。"""
        return bool(self.risks)

    @property
    def high_confidence(self) -> bool:
        """所有假设是否都是高置信度。"""
        return all(a.get("confidence", "low") == "high" for a in self.assumptions)


@dataclass(slots=True)
class ConfigChange:
    """单个配置变更记录。"""
    field: str
    before: Any
    after: Any
    why: str
    reversible: bool = True


@dataclass(slots=True)
class ConfigDiff:
    """配置差异摘要。"""
    changes: list[ConfigChange] = field(default_factory=list)
    additions: list[str] = field(default_factory=list)
    removals: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.changes and not self.additions and not self.removals

    def format_diff(self) -> str:
        """生成人类可读的差异文本。"""
        lines = []
        for change in self.changes:
            before_str = repr(change.before) if change.before is not None else "（未设置）"
            after_str = repr(change.after) if change.after is not None else "（移除）"
            lines.append(f"{change.field}: {before_str} → {after_str}")
            lines.append(f"  原因：{change.why}")
            if change.reversible:
                lines.append("  此修改可以撤销。")
        if self.additions:
            lines.append(f"新增配置项：{', '.join(self.additions)}")
        if self.removals:
            lines.append(f"移除配置项：{', '.join(self.removals)}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# AI 输出解析与校验
# ---------------------------------------------------------------------------


def _extract_json_object(text: str) -> dict[str, Any]:
    """剥离 ```json 围栏后解析 JSON 对象；失败再尝试截取首个 {…} 对象。"""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    value = safe_json_loads(stripped)
    if value is not None:
        if not isinstance(value, dict):
            raise ValueError("AI 返回的 JSON 顶层必须是对象")
        return value
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("AI 返回了无效的 JSON，且未找到可解析的对象")
    value = safe_json_loads(stripped[start:end + 1])
    if value is None:
        raise ValueError("AI 返回了无效的 JSON")
    if not isinstance(value, dict):
        raise ValueError("AI 返回的 JSON 顶层必须是对象")
    return value


_LIST_ITEM_REQUIRED: dict[str, set[str]] = {
    "assumptions": {"field", "value", "confidence"},
    "unresolved_questions": {"question"},
    "explanations": {"field", "why"},
    "risks": {"risk", "severity"},
    # recommended_actions 是字符串列表，不要求元素为对象
}


def _validate_list_elements(value: dict[str, Any]) -> None:
    """逐元素校验 AI 输出的列表字段：元素必须是对象且含必填子键。"""
    for field_name, required_subkeys in _LIST_ITEM_REQUIRED.items():
        items = value.get(field_name, [])
        if not isinstance(items, list):
            continue  # 类型校验已在上层处理
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValueError(f"AI 输出字段 {field_name}[{index}] 必须是对象")
            missing = required_subkeys - set(item)
            if missing:
                raise ValueError(
                    f"AI 输出字段 {field_name}[{index}] 缺少必填子键: {', '.join(sorted(missing))}"
                )


def parse_ai_task_output(raw_json: str, request: str = "") -> TaskDesignDraft:
    """解析并校验 AI 输出的 JSON（支持 Markdown 围栏）。

    Args:
        raw_json: 模型返回的原始文本。
        request: 原始用户需求，用于回填审计字段（模型输出本身不携带）。

    Raises:
        ValueError: JSON 解析失败或 Schema 校验不通过。
    """
    value = _extract_json_object(raw_json)

    if not isinstance(value, dict):
        raise ValueError("AI 输出必须是 JSON 对象")

    # 核心字段校验
    required_fields = {"known_requirements", "assumptions", "unresolved_questions",
                       "config_patch", "explanations", "risks", "recommended_actions"}
    missing = required_fields - set(value)
    if missing:
        raise ValueError(f"AI 输出缺少必要字段：{', '.join(missing)}")

    # C35：统一走 ai_safety.validate_ai_output（拒未声明字段 + 已有键类型校验），
    # 不再在本模块重复实现一套宽松校验。user_request 是历史兼容字段，
    # Schema 未声明（见 C29：request 由调用方回填），校验前剔除。
    validate_ai_output(
        {key: item for key, item in value.items() if key != "user_request"},
        AI_TASK_OUTPUT_SCHEMA,
    )

    # 逐元素校验（C28：拒绝非对象元素与缺子键）
    _validate_list_elements(value)

    return TaskDesignDraft(
        request=request,
        known_requirements=value.get("known_requirements", {}),
        assumptions=value.get("assumptions", []),
        unresolved_questions=value.get("unresolved_questions", []),
        config_patch=value.get("config_patch", {}),
        explanations=value.get("explanations", []),
        risks=value.get("risks", []),
        recommended_actions=value.get("recommended_actions", []),
    )


def diff_config_changes(
    current: dict[str, Any], proposed: dict[str, Any], explanations: list[dict[str, Any]]
) -> ConfigDiff:
    """计算配置差异并附带原因说明。"""
    explanation_map: dict[str, str] = {}
    for exp in explanations:
        field = str(exp.get("field", ""))
        if field:
            explanation_map[field] = str(exp.get("why", ""))

    diff = ConfigDiff()

    # 检测修改
    for key in set(current) & set(proposed):
        if current[key] != proposed[key]:
            diff.changes.append(ConfigChange(
                field=key,
                before=current[key],
                after=proposed[key],
                why=explanation_map.get(key, "AI 推荐修改"),
            ))

    # 检测新增
    for key in set(proposed) - set(current):
        diff.additions.append(key)

    # 检测移除
    for key in set(current) - set(proposed):
        diff.removals.append(key)

    return diff


def format_task_design_for_display(draft: TaskDesignDraft) -> str:
    """将 AI 任务设计格式化为用户可读的显示文本。"""
    lines = ["## 系统理解", ""]

    # 已知需求
    known = draft.known_requirements
    lines.append("### 已明确的需求")
    if known.get("url"):
        lines.append(f"- 目标网址：{known['url']}")
    if known.get("intent"):
        intent_labels = {
            "save_page": "保存页面", "collect_section": "采集栏目",
            "download_files": "下载附件/PDF", "monitor_changes": "监测内容变化",
        }
        lines.append(f"- 任务类型：{intent_labels.get(known['intent'], known['intent'])}")
    if known.get("topics"):
        lines.append(f"- 主题词：{', '.join(known['topics'])}" if isinstance(known['topics'], list) else f"- 主题词：{known['topics']}")
    if known.get("schedule"):
        lines.append(f"- 调度：{known['schedule']}")
    if known.get("explicit_requirements"):
        lines.append("- 明确要求：")
        for req in known["explicit_requirements"]:
            lines.append(f"  - {req}")
    lines.append("")

    # 系统假设
    if draft.assumptions:
        lines.append("### ⚠ 系统假设（请确认）")
        for assumption in draft.assumptions:
            conf = assumption.get("confidence", "low")
            conf_icon = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(conf, "⚪")
            lines.append(f"- {conf_icon} **{assumption.get('field', '')}**: {assumption.get('value', '')}")
            lines.append(f"  - 原因：{assumption.get('reason', '')}")
            lines.append(f"  - 置信度：{conf}")
        lines.append("")

    # 待确认问题
    if draft.unresolved_questions:
        lines.append("### ❓ 需要您确认")
        for i, q in enumerate(draft.unresolved_questions, 1):
            lines.append(f"{i}. **{q.get('question', '')}**")
            if q.get("why"):
                lines.append(f"   - 为什么需要确认：{q['why']}")
            if q.get("options"):
                lines.append(f"   - 可选：{' / '.join(q['options'])}")
            if q.get("recommendation"):
                lines.append(f"   - 推荐：{q['recommendation']}")
        lines.append("")

    # 风险
    if draft.risks:
        lines.append("### ⚠ 需要注意的风险")
        for risk in draft.risks:
            sev = {"high": "🔴 高", "medium": "🟡 中", "low": "🟢 低"}.get(risk.get("severity", ""), risk.get("severity", ""))
            lines.append(f"- {sev} **{risk.get('risk', '')}**")
            if risk.get("mitigation"):
                lines.append(f"  - 缓解措施：{risk['mitigation']}")
        lines.append("")

    # 建议操作
    if draft.recommended_actions:
        lines.append("### 建议的下一步")
        for action in draft.recommended_actions:
            lines.append(f"- {action}")
        lines.append("")

    # 安全约束
    lines.append("### 🔒 安全保证")
    for constraint in draft.safety_constraints:
        lines.append(f"- ✓ {constraint}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# AI 调用流水线
# ---------------------------------------------------------------------------


def build_task_design_messages(user_request: str, available_components: str = "", mode: str = "simple") -> list[dict[str, str]]:
    """构建发送给 AI 的消息列表。

    C34：用户需求可能是从网页/PDF 粘贴的外部片段，一律用 mark_untrusted 标注为
    “数据而非指令”后再进 prompt，避免其中的越权指令穿透系统提示的安全边界。
    """
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_PROMPT_TEMPLATE.format(
            user_request=mark_untrusted(user_request),
            available_components=available_components or "Standard 核心组件已就绪",
            mode=mode,
        )},
    ]


def _hostname(value: Any) -> str:
    """提取 URL 的主机名（小写）；非 URL 返回空串。"""
    try:
        return (urlsplit(str(value)).hostname or "").casefold().rstrip(".")
    except ValueError:
        return ""


def _domain_within(domain: str, allowed: set[str]) -> bool:
    """判断 domain 是否在允许集合内（含子域名）。"""
    domain = str(domain).casefold().strip().lstrip(".")
    return any(domain == item or domain.endswith("." + item) for item in allowed)


def validate_task_config_safety(
    config_patch: dict[str, Any], allowed_domains: list[str] | None = None
) -> list[str]:
    """校验 AI 生成的配置是否违反安全边界。

    Args:
        config_patch: AI 建议的配置修改。
        allowed_domains: 用户原始入口域名列表（seed_urls 解析所得）；None 表示跳过域名包含校验。

    Returns:
        安全违规列表；空列表表示通过。
    """
    violations: list[str] = []

    # 检查是否试图关闭安全策略
    if config_patch.get("disable_security", False):
        violations.append("AI 试图关闭网络安全策略——已阻止")

    # 递归检查是否包含明文密钥（C30：嵌套 dict/list 也不放过）
    suspicious_keys = {"api_key", "password", "secret", "token", "credential"}

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                lowered = str(key).casefold()
                if lowered in suspicious_keys or any(sk in lowered for sk in suspicious_keys):
                    if isinstance(item, str) and item and not item.startswith("secret://"):
                        violations.append(f"AI 输出了明文敏感值 {path}.{key}——已阻止")
                walk(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")

    walk(config_patch, "config_patch")

    # 域名包含校验（C30：不允许扩大用户原始入口域名）
    # “*” 白名单无条件拦截（即使未提供原始域名集合）
    for domain in config_patch.get("allowed_domains", []):
        if str(domain).strip() == "*":
            violations.append("AI 试图将所有域名加入白名单——已阻止")
    if allowed_domains:
        original = {host for url in allowed_domains if (host := _hostname(url))}
        if original:
            for url in config_patch.get("seed_urls", []):
                host = _hostname(url)
                if host and not _domain_within(host, original):
                    violations.append(f"AI 试图将入口域名扩大到 {host}——已阻止")
            for domain in config_patch.get("allowed_domains", []):
                if str(domain).strip() == "*":
                    continue  # 已在上面拦截
                if not _domain_within(str(domain), original):
                    violations.append(f"AI 试图将域名 {domain} 加入白名单——已阻止")

    return violations


def ai_task_design_audit(
    result: AIResult, draft: TaskDesignDraft, prompt_version: str = "2.1.0"
) -> dict[str, Any]:
    """记录 AI 调用的完整审计信息（C31：usage 缺失时明确标注未知费用）。"""
    usage = getattr(result, "usage", None)
    usage = usage if isinstance(usage, dict) else {}
    try:
        total_tokens = int(usage.get("total_tokens", 0) or 0)
    except (TypeError, ValueError):
        total_tokens = 0
    if total_tokens > 0:
        # 与 AIBudget 记账使用同一单价常量，避免审计与预算两套口径
        cost = total_tokens * _ESTIMATED_COST_PER_TOKEN
        cost_note = f"按 {_ESTIMATED_COST_PER_TOKEN}/token 粗略估算（{total_tokens} tokens），非账单实际金额"
    else:
        cost = 0.0
        cost_note = "未知费用（provider 未返回 usage）"
    record = ai_audit_record(
        provider=str(getattr(result, "provider", "unknown")),
        model=str(getattr(result, "model", "")),
        prompt_version=prompt_version,
        parameters={
            "request": draft.request[:500],
            "assumptions_count": len(draft.assumptions),
            "questions_count": len(draft.unresolved_questions),
            "risks_count": len(draft.risks),
        },
        response=json.dumps(asdict(draft), ensure_ascii=False),
        cost=cost,
    )
    record["cost_note"] = cost_note
    return record


def _append_ai_audit(record: dict[str, Any]) -> None:
    """追加一条 AI 审计记录到 JSONL（C32：成功/失败/拦截三路落盘）。

    审计失败不影响主流程。
    """
    import time

    from ..core.runtime_paths import portable_data_root

    record = {**record, "ts": time.time()}
    try:
        path = portable_data_root() / ".omnicrawler" / "ai-logs" / "ai-audit.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with _AUDIT_LOCK, path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass
