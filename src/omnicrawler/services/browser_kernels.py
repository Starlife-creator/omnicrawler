"""浏览器内核清单：把「槽位能提供的」与「组件市场里有的」合成一张表。

## 为什么需要它

系统里原本有两条互不相通的线：

```
能力槽位（CapabilitySlotRegistry）  → 这次能用哪个内核启动
组件市场（market/components/）      → 市面上有哪些内核可装
```

用户需要的是**一张表**：哪些已装、哪些可装、各自多大、来自哪、可信不可信。
这个模块做这个合成。它不启动浏览器、不联网、不写盘 —— 纯读。

## 三种来源（§15.16.5 的信任分级）

| 来源 | 含义 | 信任 |
|---|---|---|
| `builtin` | 随包内置 | ✅ 构建时签名 |
| `market` | 市场配方 + 上游 CDN | ✅ 签名 + sha256 |
| `local` | 用户自己指路径 | ⚠️ **未验证** |

`local` 必须显式带 `trust: unverified`，并且**在列表里就可见** ——
隐藏状态比不做还糟。

## 与槽位的关系

槽位回答「这次用哪个」（`preferred_id`），本模块回答「有哪些可选」。
两者不重叠：本模块不决定默认值，只负责把选项列全、把话说准。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: 来源 → 面向用户的信任标注。
_TRUST_LABEL = {
    "builtin": "内置（构建时签名）",
    "market": "市场（签名 + 哈希）",
    "local": "本地导入（未验证）",
}


@dataclass(frozen=True, slots=True)
class KernelOption:
    """一个可选的浏览器内核。"""

    provider_id: str
    display_name: str
    engine_family: str
    source: str
    available: bool
    trust: str
    size_bytes: int | None = None
    description: str = ""
    #: 组件 id（市场侧）。内置与市场来源会有；纯槽位 provider 可能没有。
    component_id: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "engine_family": self.engine_family,
            "source": self.source,
            "available": self.available,
            "trust": self.trust,
            "trust_label": _TRUST_LABEL.get(self.source, "未知来源"),
            "size_bytes": self.size_bytes,
            "description": self.description,
            "component_id": self.component_id,
            "warnings": list(self.warnings),
        }


def _components_by_capability(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """把 catalog 里的组件按提供的能力建索引。"""
    index: dict[str, dict[str, Any]] = {}
    for component in catalog.get("components") or []:
        for capability in component.get("provides") or []:
            index[str(capability).strip().lower()] = component
    return index


def _artifact_source(component: dict[str, Any]) -> str:
    """组件构件的来源：全内置 ⇒ builtin，否则 market。"""
    artifacts = component.get("artifacts") or []
    if artifacts and all(
        str(a.get("source") or "remote").lower() == "builtin" for a in artifacts
    ):
        return "builtin"
    return "market"


def _match_component(
    index: dict[str, dict[str, Any]], engine: str, *, provider_id: str
) -> dict[str, Any] | None:
    """把 provider 关联到市场里的**同一个**内核组件。

    **不能只看能力名**：`playwright` 与 `selenium` 都声明
    `engine_family=chromium`，于是都匹配到 `browser.chromium`，
    结果 `selenium` 被标成「内置」并挂上 Chromium 的 415 MiB（实测踩到）。

    正确判据：**provider 的引擎实现方式必须与组件描述的一致**。
    `chromium-builtin` 是 Playwright 的 Chromium，不是 ChromeDriver 的。
    所以要求 provider_id 里出现的实现名（playwright/selenium）
    与组件 id 的语义相符 —— 组件 id 里没有实现名时，默认归给 playwright。
    """
    if not engine:
        return None
    component = index.get(f"browser.{engine}")
    if component is None:
        return None

    component_id = str(component.get("id") or "").lower()
    pid = provider_id.lower()
    # 组件明确写了实现方式 ⇒ 必须一致
    for implementation in ("playwright", "selenium"):
        if implementation in component_id:
            return component if implementation in pid else None
    # 组件没写实现方式（如 `chromium-builtin`）⇒ 只归给 playwright 系，
    # 不归给 selenium 系（后者的字节来自 WebDriver，与组件不是同一份）
    if "selenium" in pid:
        return None
    return component


def list_kernels(
    *,
    registry: Any = None,
    catalog: dict[str, Any] | None = None,
    local_kernels: list[dict[str, Any]] | None = None,
) -> list[KernelOption]:
    """合成内核清单。

    :param registry: `CapabilitySlotRegistry`；缺省用全局默认注册表。
    :param catalog: 市场 catalog 数据；缺省不读盘（只列槽位里的）。
    :param local_kernels: 用户本地导入的内核（未验证）。

    排序：**可用的在前**，其次按 source 可信度（builtin > market > local），
    最后按 provider_id 稳定排序。**不用 priority 排** —— 那是内部调度用的，
    对用户没有意义，混进列表排序会让人看不懂为什么是这个顺序。
    """
    if registry is None:
        from ..core.capabilities import default_capability_slots

        registry = default_capability_slots()

    slot = registry.slot("browser")
    providers = list(slot.providers()) if slot is not None else []

    component_index = _components_by_capability(catalog or {})
    options: list[KernelOption] = []

    for provider in providers:
        metadata = dict(getattr(provider, "metadata", {}) or {})
        engine = str(metadata.get("engine_family") or "")

        # **组件归属不能只看能力名**：`selenium` 与 `playwright` 都是
        # `browser.chromium`，按能力名匹配会让 selenium 挂上 Chromium 的
        # 大小与「内置」标记（实测踩到）。
        #
        # 只有当 provider 的 id 与该组件的 id 语义对应时才关联：
        # 组件 id 形如 `chromium-builtin`，provider id 形如 `playwright`，
        # 两者的公共锚点是**内核名**（chromium），而不是能力名。
        #
        # 判据：组件 id 里含内核名，且 provider 的 id 里也含同一个内核名，
        # 或 provider 的 driver_kind 与该组件的 protocol 一致。
        component = _match_component(component_index, engine, provider_id=str(provider.provider_id))

        # **来源以组件为准，槽位 metadata 只作兜底**。
        #
        # `metadata["bundled"]` 说的是「这个 provider 的内核是否随包」，
        # 不是「字节从哪来」。用它直接当 source 会把 `selenium` 标成「内置」
        # 并挂上 Chromium 的大小（实测踩到）——
        # 因为 selenium 的 metadata 里也有 engine_family=chromium，
        # 于是匹配到了 chromium-builtin 组件。
        #
        # 正确判据：**只有在能力名精确匹配某个组件的 provides，且该组件
        # 确实全内置时**，才是 builtin；否则一律 market（可下载）。
        source = "market"
        if component is not None:
            source = _artifact_source(component)

        warnings: list[str] = []
        size = None
        component_id = None
        if component is not None:
            component_id = str(component.get("id") or "") or None
            raw_size = component.get("declared_total_bytes")
            size = int(raw_size) if isinstance(raw_size, int) else None

        try:
            available = bool(provider.is_available())
        except Exception:  # noqa: BLE001 - 探测失败按不可用处理，不能崩溃
            available = False
            warnings.append("可用性探测异常，已按不可用处理")

        if not available:
            warnings.append("内核文件缺失或未安装")

        options.append(
            KernelOption(
                provider_id=str(provider.provider_id),
                display_name=str(getattr(provider, "display_name", provider.provider_id)),
                engine_family=engine,
                source=source,
                available=available,
                trust=source,
                size_bytes=size,
                description=str(getattr(provider, "description", "") or ""),
                component_id=component_id,
                warnings=tuple(warnings),
            )
        )

    # 本地导入的内核：**必须出现在同一张表里** —— 隐藏状态比不做还糟。
    for entry in local_kernels or []:
        path_text = str(entry.get("path") or "")
        exists = bool(path_text) and Path(path_text).is_file()
        warnings = ["未验证来源，仅本机使用"]
        if not exists:
            warnings.append("文件不存在")
        options.append(
            KernelOption(
                provider_id=f"local:{entry.get('id') or Path(path_text).name or 'kernel'}",
                display_name=str(entry.get("label") or Path(path_text).name or "本地内核"),
                engine_family=str(entry.get("engine_family") or ""),
                source="local",
                available=exists,
                trust="local",
                size_bytes=None,
                description="用户本地导入的浏览器内核",
                component_id=None,
                warnings=tuple(warnings),
            )
        )

    _source_rank = {"builtin": 0, "market": 1, "local": 2}
    options.sort(
        key=lambda option: (
            0 if option.available else 1,
            _source_rank.get(option.source, 9),
            option.provider_id,
        )
    )
    return options


def describe_kernels(options: list[KernelOption]) -> str:
    """渲染成 CLI 文本（人类可读，三列对齐）。"""
    if not options:
        return "没有可用的浏览器内核。"

    lines = ["浏览器内核："]
    for option in options:
        mark = "✓" if option.available else "✗"
        size = (
            f"{option.size_bytes // 1024 // 1024} MiB"
            if option.size_bytes
            else "—"
        )
        lines.append(
            f"  {mark} {option.display_name}"
            f"  [{_TRUST_LABEL.get(option.source, option.source)}]"
            f"  {size}"
        )
        if option.description:
            lines.append(f"      {option.description}")
        for warning in option.warnings:
            lines.append(f"      ⚠ {warning}")
    return "\n".join(lines)
