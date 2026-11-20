"""插件 ↔ 组件的**能力契约**：谁的 `required_capabilities` 由谁满足。

## 断链问题

市场里两条平行线：

```
插件 → required_capabilities: {"ocr.paddle": ">=1"}   ← 我需要什么
组件 → provides:             ["ocr.paddle"]           ← 我提供什么
```

两者此前**没有任何代码把它们对上**。用户装了插件、没装组件时，只会在运行期
撞上 `ModuleNotFoundError`；市场也无法判断「这个插件的必需能力有没有组件兜底」。

## 本模块做什么

三件事，都是纯函数（不联网、不写盘）：

1. `resolve(plugins, components)` —— 算出每个插件的必需能力被哪些组件覆盖、
   还缺哪些；
2. `check_market(...)` —— 市场发版门禁用：任何插件的必需能力若无人覆盖，
   **判红**（不让一个注定跑不起来的组合发出去）；
3. `install_hint(...)` —— 宿主用：告诉用户「装 X 组件可解锁此插件」。

## 判据

* 能力名按**小写点分**归一（`OCR.Paddle` ≡ `ocr.paddle`）；
* 组件的 `provides` 是它的**承诺**；插件 `required_capabilities` 是它的**需求**；
* **空 provides 的组件视为不提供任何能力**（不是「全满足」）。

## ★ 两类能力必须分开：宿主核心 vs 组件

插件的 `required_capabilities` 里绝大多数是**宿主 SDK 提供**的核心能力
（`records.page` / `view.richtext` / `state.get` / `surface.background.*` …）——
它们由 `omnicrawler.sdk` 的运行时提供，**与组件无关**。

如果把核心能力也拿去和组件的 `provides` 比对，会得出「市场里 5 个插件都缺能力」
这种**错误结论**（真实现状见下）。所以本模块只判**组件式能力**：

* 组件式能力的命名带域前缀，当前约定：`ocr.*` / `pdf.*` / `browser.*` / `model.*`
* 其余一律视为宿主核心能力，**不参与组件匹配**（由 SDK 自己保证）

这个划分写在常量里而不是散落在判断条件里，是为了以后加新域时有唯一改动点。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: 组件式能力的**域前缀**。只有以这些前缀开头的能力才拿去和组件 `provides` 比对。
#:
#: 其余（`records.page` / `view.richtext` / `state.get` / `surface.background.*` …）
#: 由宿主 SDK 提供，与组件无关 —— 拿它们去比会得出「市场里一半插件都废了」的
#: 错误结论。
COMPONENT_CAPABILITY_DOMAINS: tuple[str, ...] = ("ocr.", "pdf.", "browser.", "model.")


def is_component_capability(name: str) -> bool:
    """该能力是否需要**由组件**满足（而不是宿主核心提供）。"""
    normalized = _normalize(name)
    return any(normalized.startswith(domain) for domain in COMPONENT_CAPABILITY_DOMAINS)


def _normalize(name: str) -> str:
    return str(name).strip().lower()


@dataclass
class PluginCapabilityCheck:
    """一个插件的能力满足情况。"""

    plugin_id: str
    required: list[str] = field(default_factory=list)
    satisfied_by: dict[str, list[str]] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing


@dataclass
class MarketCapabilityReport:
    """整个市场的能力覆盖情况。"""

    checks: list[PluginCapabilityCheck] = field(default_factory=list)
    unused_components: dict[str, list[str]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    @property
    def broken(self) -> list[PluginCapabilityCheck]:
        return [check for check in self.checks if not check.ok]


def component_provisions(components: list[dict[str, Any]]) -> dict[str, list[str]]:
    """把组件列表折成 `{能力名: [组件 id ...]}`。"""
    provided: dict[str, list[str]] = {}
    for component in components:
        cid = str(component.get("id") or "")
        for capability in component.get("provides") or []:
            key = _normalize(capability)
            if not key:
                continue
            provided.setdefault(key, [])
            if cid and cid not in provided[key]:
                provided[key].append(cid)
    return provided


def resolve(
    plugins: list[dict[str, Any]], components: list[dict[str, Any]]
) -> MarketCapabilityReport:
    """算出每个插件的必需能力覆盖情况。"""
    provided = component_provisions(components)
    report = MarketCapabilityReport()

    for plugin in plugins:
        pid = str(plugin.get("id") or "")
        required_raw = plugin.get("required_capabilities") or {}
        if not isinstance(required_raw, dict):
            required_raw = {}
        # 只取**组件式**能力；核心能力由宿主 SDK 保证，不参与这里的匹配。
        required = [
            _normalize(name) for name in required_raw if is_component_capability(name)
        ]
        check = PluginCapabilityCheck(plugin_id=pid, required=required)
        for capability in required:
            owners = provided.get(capability) or []
            if owners:
                check.satisfied_by[capability] = list(owners)
            else:
                check.missing.append(capability)
        report.checks.append(check)

    # 反向：哪些组件的能力**没有任何插件需要**（可能是白装的大件）
    all_required = {cap for check in report.checks for cap in check.required}
    for capability, owners in provided.items():
        if capability not in all_required:
            report.unused_components[capability] = list(owners)
    return report


def install_hint(check: PluginCapabilityCheck, components: list[dict[str, Any]]) -> str:
    """给用户的可执行提示：装哪个组件能解锁。

    没有对应组件时如实说「市场里没有能提供它的组件」—— 不要编一个。
    """
    if check.ok:
        return ""
    provided = component_provisions(components)
    lines = [f"插件 {check.plugin_id} 缺少 {len(check.missing)} 项能力："]
    for capability in check.missing:
        owners = provided.get(capability)
        if owners:
            lines.append(f"  - {capability}：可安装组件 {', '.join(owners)}")
        else:
            lines.append(f"  - {capability}：市场里暂无组件提供此项能力")
    return "\n".join(lines)
