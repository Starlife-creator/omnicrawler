"""Pluggable capability slot & equal-citizen provider registry.

Built-in implementations, plugins, and components register into named capability
slots (`html_parser`, `browser`, `ocr`, `captcha_ocr`, `table_storage`,
`framework_bridge`, etc.) on strictly equal footing (`source` is descriptive
metadata, never a privilege hierarchy).

Import-safety rule: this module lives in `omnicrawler.core` and MUST NOT import
any optional third-party runtime directly; availability probes rely exclusively
on `importlib.util.find_spec` or caller-supplied callbacks.
"""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

ProviderSource = Literal["builtin", "plugin", "component"]
VALID_PROVIDER_SOURCES: frozenset[str] = frozenset({"builtin", "plugin", "component"})


def _spec_available(*module_names: str) -> Callable[[], bool]:
    """Return a lightweight, import-free availability check for module specs."""

    def _probe() -> bool:
        return all(importlib.util.find_spec(name) is not None for name in module_names)

    return _probe


@dataclass(frozen=True, slots=True)
class CapabilityProvider:
    """A single provider (builtin, plugin, or component) for a capability slot."""

    provider_id: str
    slot_name: str
    source: ProviderSource
    display_name: str
    priority: int = 100
    availability_check: Callable[[], bool] | None = None
    factory: Callable[..., Any] | None = None
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.provider_id or not self.provider_id.strip():
            raise ValueError("provider_id 不能为空")
        if not self.slot_name or not self.slot_name.strip():
            raise ValueError("slot_name 不能为空")
        if self.source not in VALID_PROVIDER_SOURCES:
            raise ValueError(
                f"未知提供者来源 {self.source!r}（仅支持 builtin / plugin / component 平等来源）"
            )

    def is_available(self) -> bool:
        """Check whether the provider's runtime dependencies are ready."""
        if self.availability_check is None:
            return True
        try:
            return bool(self.availability_check())
        except (ImportError, OSError, RuntimeError, ValueError):
            return False


class CapabilitySlot:
    """Named capability extension point where builtin/plugin/component compete equally."""

    def __init__(self, slot_name: str, description: str = "") -> None:
        if not slot_name or not slot_name.strip():
            raise ValueError("slot_name 不能为空")
        self.slot_name = slot_name.strip()
        self.description = description
        self._providers: dict[str, CapabilityProvider] = {}
        self._preferred_id: str | None = None

    def register(self, provider: CapabilityProvider) -> None:
        """Register or update a provider in this slot."""
        if provider.slot_name != self.slot_name:
            raise ValueError(
                f"提供者 {provider.provider_id!r} 声明槽位 {provider.slot_name!r} "
                f"与目标槽位 {self.slot_name!r} 不一致"
            )
        self._providers[provider.provider_id] = provider

    def unregister(self, provider_id: str) -> bool:
        """Remove a provider by id; clears active selection if it pointed to it."""
        removed = self._providers.pop(provider_id, None) is not None
        if removed and self._preferred_id == provider_id:
            self._preferred_id = None
        return removed

    def get(self, provider_id: str) -> CapabilityProvider | None:
        return self._providers.get(provider_id)

    def providers(self, *, only_available: bool = False) -> list[CapabilityProvider]:
        """Return registered providers ordered by priority (descending) then id."""
        items = list(self._providers.values())
        if only_available:
            items = [item for item in items if item.is_available()]
        items.sort(key=lambda item: (-item.priority, item.provider_id))
        return items

    def available(self) -> list[CapabilityProvider]:
        """Alias returning ready providers ordered by priority."""
        return self.providers(only_available=True)

    @property
    def preferred_id(self) -> str | None:
        return self._preferred_id

    def set_preferred(self, provider_id: str | None) -> None:
        """Set preferred provider id (`None` or `'auto'` restores priority selection)."""
        normalized = (provider_id or "").strip()
        if not normalized or normalized == "auto":
            self._preferred_id = None
            return
        self._preferred_id = normalized

    def active(
        self,
        preferred_id: str | None = None,
        *,
        strict: bool = False,
    ) -> CapabilityProvider | None:
        """Resolve the active provider for this call or slot preference.

        - Explicit ``preferred_id`` (other than ``'auto'``) takes precedence over
          slot-level ``preferred_id``.
        - If ``strict=True``, an unavailable or unknown explicit provider returns ``None``
          instead of silently falling back to another engine.
        - Otherwise falls back to the highest-priority available provider.
        """
        explicit = (preferred_id or "").strip()
        target_id = (
            explicit
            if explicit and explicit != "auto"
            else (self._preferred_id or "")
        )
        if target_id:
            chosen = self._providers.get(target_id)
            if chosen is not None and chosen.is_available():
                return chosen
            if strict:
                return None
        ready = self.available()
        return ready[0] if ready else None


class CapabilitySlotRegistry:
    """Registry of named capability slots with manifest auto-discovery."""

    def __init__(self) -> None:
        self._slots: dict[str, CapabilitySlot] = {}

    def ensure_slot(self, slot_name: str, description: str = "") -> CapabilitySlot:
        slot = self._slots.get(slot_name)
        if slot is None:
            slot = CapabilitySlot(slot_name, description=description)
            self._slots[slot_name] = slot
        elif description and not slot.description:
            slot.description = description
        return slot

    def slot(self, slot_name: str) -> CapabilitySlot | None:
        return self._slots.get(slot_name)

    def slot_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._slots))

    def register(self, provider: CapabilityProvider) -> CapabilitySlot:
        slot = self.ensure_slot(provider.slot_name)
        slot.register(provider)
        return slot

    def register_manifest_entries(
        self,
        entries: Iterable[Mapping[str, Any]],
        *,
        source: Literal["plugin", "component"],
    ) -> int:
        """Register providers declared via ``provides`` in plugin/component manifests.

        Supported ``provides`` item shapes:
        - ``"ocr"`` (string slot name)
        - ``{"slot": "ocr", "id": "custom-id", "display_name": "...", "priority": 130}``
        Also recognizes component ``runtime.engine`` for ``ocr-file-v1`` recipes so
        existing installed OCR components participate without manifest migration.
        """
        count = 0
        for raw in entries:
            base_id = str(raw.get("id") or raw.get("name") or "").strip()
            if not base_id:
                continue
            base_title = str(raw.get("display_name") or raw.get("title") or base_id).strip()
            purpose = str(raw.get("description") or raw.get("purpose") or "").strip()
            provides_raw = raw.get("provides", [])
            declared_items: list[Mapping[str, Any] | str] = (
                list(provides_raw) if isinstance(provides_raw, (list, tuple)) else []
            )
            runtime = raw.get("runtime")
            if (
                not declared_items
                and source == "component"
                and isinstance(runtime, Mapping)
                and runtime.get("protocol") == "ocr-file-v1"
            ):
                declared_items = ["ocr"]

            for item in declared_items:
                if isinstance(item, str) and item.strip():
                    slot_name = item.strip()
                    provider_id = base_id
                    display_name = base_title
                    priority = int(raw.get("priority", 110))
                elif isinstance(item, Mapping) and str(item.get("slot", "")).strip():
                    slot_name = str(item["slot"]).strip()
                    provider_id = str(item.get("id") or base_id).strip()
                    display_name = str(item.get("display_name") or base_title).strip()
                    priority = int(item.get("priority", raw.get("priority", 110)))
                else:
                    continue

                self.register(
                    CapabilityProvider(
                        provider_id=provider_id,
                        slot_name=slot_name,
                        source=source,
                        display_name=display_name,
                        priority=priority,
                        description=purpose,
                        metadata={"manifest_id": base_id},
                    )
                )
                count += 1
        return count


def build_builtin_slot_registry() -> CapabilitySlotRegistry:
    """Create a fresh registry populated with standard built-in capability providers."""
    reg = CapabilitySlotRegistry()

    # 1. HTML DOM Parser slot (selectolax Lexbor fast-path + lxml standard fallback)
    html_slot = reg.ensure_slot("html_parser", "HTML DOM 解析与结构特征提取后端")
    html_slot.register(
        CapabilityProvider(
            provider_id="selectolax",
            slot_name="html_parser",
            source="builtin",
            display_name="Selectolax (Lexbor 高速解析)",
            priority=120,
            availability_check=_spec_available("selectolax"),
            description="基于 C Lexbor 引擎的高吞吐 HTML 解析路径",
        )
    )
    html_slot.register(
        CapabilityProvider(
            provider_id="lxml",
            slot_name="html_parser",
            source="builtin",
            display_name="lxml.html (标准兼容解析)",
            priority=100,
            availability_check=_spec_available("lxml"),
            description="支持完整 XPath 与容错树遍历的标准解析器",
        )
    )

    # 2. Browser automation engine slot
    browser_slot = reg.ensure_slot("browser", "动态网页渲染与交互浏览器引擎")
    browser_slot.register(
        CapabilityProvider(
            provider_id="playwright",
            slot_name="browser",
            source="builtin",
            display_name="Playwright (Chromium)",
            priority=120,
            availability_check=_spec_available("playwright"),
            description="默认浏览器自动化引擎，支持请求级出网拦截与网络捕获",
        )
    )
    browser_slot.register(
        CapabilityProvider(
            provider_id="selenium",
            slot_name="browser",
            source="builtin",
            display_name="Selenium WebDriver (BiDi)",
            priority=90,
            availability_check=_spec_available("selenium"),
            description="备用 WebDriver 引擎（需开启 BiDi 出网守卫）",
        )
    )

    # 3. OCR engine slot
    ocr_slot = reg.ensure_slot("ocr", "文档与图像光学字符识别引擎")
    ocr_slot.register(
        CapabilityProvider(
            provider_id="paddleocr",
            slot_name="ocr",
            source="builtin",
            display_name="PaddleOCR v3",
            priority=120,
            availability_check=_spec_available("paddleocr", "paddle", "numpy"),
            description="版面结构与中英混合高精度离线识别",
        )
    )
    ocr_slot.register(
        CapabilityProvider(
            provider_id="tesseract",
            slot_name="ocr",
            source="builtin",
            display_name="Tesseract OCR",
            priority=100,
            availability_check=_spec_available("pytesseract", "PIL"),
            description="轻量跨平台多语种字符识别",
        )
    )

    # 4. Captcha OCR slot (optional heavy extra decoupled behind slot)
    captcha_slot = reg.ensure_slot("captcha_ocr", "图形验证码离线识别后端")
    captcha_slot.register(
        CapabilityProvider(
            provider_id="ddddocr",
            slot_name="captcha_ocr",
            source="builtin",
            display_name="ddddocr (ONNX Runtime)",
            priority=100,
            availability_check=_spec_available("ddddocr", "onnxruntime"),
            description="可选本地 ONNX 验证码识别器",
        )
    )

    # 5. Table storage / export backend slot
    storage_slot = reg.ensure_slot("table_storage", "结构化表格读写与导出存储后端")
    for pid, title, prio, reqs, desc in (
        ("csv", "CSV 流式表格", 120, (), "零额外依赖流式 CSV 读写（含公式注入防护）"),
        ("jsonl", "JSON Lines", 115, (), "零额外依赖逐行 JSON 记录流"),
        ("xlsx", "Excel 工作簿 (.xlsx)", 110, ("openpyxl",), "多表头样式友好的办公表格导出"),
        ("parquet", "Apache Parquet", 105, ("pyarrow",), "列式高压缩大数据归档格式"),
        ("duckdb", "DuckDB 本地分析库", 100, ("duckdb",), "支持 SQL 即席聚合的本地单文件分析库"),
    ):
        storage_slot.register(
            CapabilityProvider(
                provider_id=pid,
                slot_name="table_storage",
                source="builtin",
                display_name=title,
                priority=prio,
                availability_check=_spec_available(*reqs) if reqs else None,
                description=desc,
            )
        )

    # 6. Third-party crawl framework bridges (optional extras decoupled behind slot)
    bridge_slot = reg.ensure_slot("framework_bridge", "外部采集框架桥接适配器")
    bridge_slot.register(
        CapabilityProvider(
            provider_id="scrapy",
            slot_name="framework_bridge",
            source="builtin",
            display_name="Scrapy Spider 桥接",
            priority=100,
            availability_check=_spec_available("scrapy"),
            description="可选 Scrapy 爬虫规则桥接",
        )
    )
    bridge_slot.register(
        CapabilityProvider(
            provider_id="crawl4ai",
            slot_name="framework_bridge",
            source="builtin",
            display_name="Crawl4AI 桥接",
            priority=95,
            availability_check=_spec_available("crawl4ai"),
            description="可选 Crawl4AI 提取桥接",
        )
    )
    return reg


_DEFAULT_REGISTRY: CapabilitySlotRegistry | None = None


def default_capability_slots(*, refresh: bool = False) -> CapabilitySlotRegistry:
    """Return the process-wide default capability slot registry."""
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None or refresh:
        _DEFAULT_REGISTRY = build_builtin_slot_registry()
    return _DEFAULT_REGISTRY


def discover_installed_providers(
    *,
    plugins_dir: Path | None = None,
    components_manifest: Path | None = None,
    registry: CapabilitySlotRegistry | None = None,
) -> CapabilitySlotRegistry:
    """Scan local plugin manifests and component ``installed.json`` into ``registry``."""
    target = registry if registry is not None else default_capability_slots()

    if plugins_dir is not None and plugins_dir.is_dir():
        plugin_manifests: list[dict[str, Any]] = []
        for manifest_file in sorted(plugins_dir.glob("*/plugin.json")):
            try:
                raw = json.loads(manifest_file.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    plugin_manifests.append(raw)
            except (OSError, ValueError):
                continue
        target.register_manifest_entries(plugin_manifests, source="plugin")

    if components_manifest is not None and components_manifest.is_file():
        try:
            doc = json.loads(components_manifest.read_text(encoding="utf-8"))
            if isinstance(doc, dict):
                comp_entries = [v for v in doc.values() if isinstance(v, dict)]
                target.register_manifest_entries(comp_entries, source="component")
        except (OSError, ValueError):
            pass

    return target
