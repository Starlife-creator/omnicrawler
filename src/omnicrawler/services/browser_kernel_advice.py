"""任务失败时的「换内核再试」建议（§15.16.7②）。

## 为什么需要它

静态的内核选择器**没人会去看** —— 用户不知道该在什么时候换内核。
真正有用的时机是**任务失败的那一刻**：

```
采集失败：页面脚本在 Chromium 下超时
    ↓
「要不要换 Firefox 内核再试一次？」[试试] [忽略]
```

这样用户不需要先理解「内核差异」，而是在遇到问题时被引导。

## 判据设计（为什么不是「一失败就建议换」）

盲目建议换内核是**有害的**：用户会以为是浏览器问题，实际可能是网络、
选择器、或站点本身改了。所以只有**症状指向渲染层**时才建议：

| 症状 | 建议换内核 | 理由 |
|---|---|---|
| 页面超时 / 脚本执行超时 | ✅ | 不同内核的 JS 引擎与超时行为不同 |
| 渲染结果为空但 HTTP 200 | ✅ | 典型的内核兼容问题（某些站点只在特定内核渲染） |
| 反爬拦截（403 / 验证码） | ❌ **明确不建议** | 换内核不解决风控，且本项目明确不做反检测 |
| 网络错误 / DNS 失败 | ❌ | 与内核无关 |
| 选择器未命中 | ❌ | 是页面结构问题，换内核不会变 |
| 解析/导出失败 | ❌ | 内核已经交付了页面，问题在后续环节 |

**「反爬拦截不建议换内核」这条是刻意的**：项目已明确拒绝
`stealth-browser-mcp` 那条路（规避风控），换内核不能成为变相反检测的入口。
"""

from __future__ import annotations

from dataclasses import dataclass

#: 症状 → 是否值得建议换内核。键是**症状分类**，不是原始错误文本。
_SWITCH_WORTHY = {
    "navigation_timeout": True,
    "script_timeout": True,
    "empty_render": True,
    "render_mismatch": True,
    # 以下明确不建议 —— 换内核解决不了，建议了只会误导
    "anti_bot": False,
    "network_error": False,
    "selector_miss": False,
    "parse_error": False,
    "export_error": False,
    "unknown": False,
}

#: 症状分类 → 面向用户的话术。
_SYMPTOM_TEXT = {
    "navigation_timeout": "页面加载超时",
    "script_timeout": "页面脚本执行超时",
    "empty_render": "页面内容为空（HTTP 却是成功的）",
    "render_mismatch": "渲染结果与预期不符",
    "anti_bot": "站点返回了反爬拦截",
    "network_error": "网络请求失败",
    "selector_miss": "没找到目标元素",
    "parse_error": "内容解析失败",
    "export_error": "导出失败",
    "unknown": "未知错误",
}

#: 从错误文本推断症状分类（顺序即优先级，先匹配到的算数）。
_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("anti_bot", ("captcha", "验证码", "403 forbidden", "access denied", "cloudflare", "反爬")),
    ("navigation_timeout", ("navigation timeout", "page.goto", "net::err", "加载超时", "goto timeout")),
    ("script_timeout", ("script timeout", "evaluate", "超时", "timeout")),
    ("empty_render", ("empty", "内容为空", "no content", "blank")),
    ("selector_miss", ("selector", "no element", "未找到", "选择器")),
    ("network_error", ("connection", "dns", "socket", "网络", "refused", "unreachable")),
    ("parse_error", ("parse", "解析", "json")),
    ("export_error", ("export", "导出", "write")),
)


@dataclass(frozen=True, slots=True)
class SwitchKernelAdvice:
    """是否建议换内核，以及为什么。"""

    suggest: bool
    symptom: str
    reason: str
    #: 建议的目标内核（空 = 不指定，由调用方按可用列表决定）
    target_engine: str = ""

    @property
    def symptom_text(self) -> str:
        return _SYMPTOM_TEXT.get(self.symptom, self.symptom)

    def message(self) -> str:
        """面向用户的提示文本（不含按钮）。"""
        if not self.suggest:
            return ""
        tail = f"，改用 {self.target_engine}" if self.target_engine else ""
        return f"任务失败：{self.symptom_text}。这可能与浏览器内核有关{tail}再试一次？"


def classify_failure(error_text: str) -> str:
    """从错误文本推断症状分类。认不出时返回 `unknown`。"""
    lowered = (error_text or "").lower()
    for symptom, needles in _PATTERNS:
        if any(needle in lowered for needle in needles):
            return symptom
    return "unknown"


def advise_switch(
    error_text: str,
    *,
    current_engine: str = "",
    available_engines: tuple[str, ...] | list[str] = ("chromium", "firefox", "webkit"),
) -> SwitchKernelAdvice:
    """给出「要不要换内核」的建议。

    :param error_text: 任务的错误文本（原始日志即可）
    :param current_engine: 当前用的内核族（chromium / firefox / webkit）
    :param available_engines: 本机可用的内核族

    **没有别的内核可选时不建议** —— 建议一个装不上的东西只会让人困惑。
    """
    symptom = classify_failure(error_text)
    worthy = _SWITCH_WORTHY.get(symptom, False)

    if not worthy:
        return SwitchKernelAdvice(
            suggest=False,
            symptom=symptom,
            reason=_reason_not_suggested(symptom),
        )

    alternatives = [e for e in available_engines if e != current_engine]
    if not alternatives:
        return SwitchKernelAdvice(
            suggest=False,
            symptom=symptom,
            reason="本机没有其他可用内核，换内核无法执行",
        )

    return SwitchKernelAdvice(
        suggest=True,
        symptom=symptom,
        reason=f"{_SYMPTOM_TEXT.get(symptom, symptom)} 可能与该内核的渲染行为有关",
        target_engine=alternatives[0],
    )


def _reason_not_suggested(symptom: str) -> str:
    """为什么不建议换内核 —— 说清原因，避免用户以为是漏了提示。"""
    return {
        "anti_bot": "站点做了风控拦截，换内核不解决问题（本项目不做反检测）",
        "network_error": "网络层错误，与浏览器内核无关",
        "selector_miss": "页面结构问题，换内核不会改变选择器匹配",
        "parse_error": "解析环节的问题，内核已正常交付页面",
        "export_error": "导出环节的问题，与内核无关",
    }.get(symptom, "该症状与浏览器内核无关，换内核不会改善")
