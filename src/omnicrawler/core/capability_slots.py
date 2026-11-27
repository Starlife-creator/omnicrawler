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
import os
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


#: Playwright 各内核在 `ms-playwright/` 下的目录前缀。
#:
#: **为什么需要它**：`_spec_available("playwright")` 只检查 **Python 包**
#: 是否安装，三个内核（chromium / firefox / webkit）共用同一个包 ——
#: 于是 firefox 与 webkit 的二进制根本没有时，也报告 `available=True`。
#:
#: 实测（2026-11-20 本机）：`ms-playwright/` 下只有 `chromium-1228`，
#: 但 `playwright-firefox` 与 `playwright-webkit` 都返回 True。
#: 用户在界面上看到「可用」、选了它、运行时才失败 —— 正是
#: §15.2「声明必须能从字节推导」要禁止的那类问题。
#: Selenium 系 provider 需要的外部驱动（可执行文件名，按平台）。
#:
#: **描述自己写着「需配合 X 运行」，探测就必须检查 X**：
#: `selenium` / `selenium-firefox` 原先只 `_spec_available("selenium")`，
#: 于是驱动没装也报可用。用户选了它，运行时才失败。
_WEBDRIVER_NAMES = {
    "chromedriver": ("chromedriver.exe", "chromedriver"),
    "geckodriver": ("geckodriver.exe", "geckodriver"),
}


#: 探测结果缓存：`(名称) -> 解析到的路径 或 None`。
#:
#: **为什么必须缓存**（2026-11-25 实测）：本机 PATH 有 36 条目录，
#: `shutil.which("chromedriver.exe")` 实测 **21 ms**（走完 36 个目录的
#: `nt._path_exists`）。而 `slot_combo_items()` 每次刷新下拉框都会对
#: 每个 provider 调 `is_available()` —— 6 个槽位 × 18 个条目 = 98.5 ms，
#: 其中 82.8 ms 全花在这 4 次 `shutil.which` 上。
#:
#: 缓存的语义边界：**同一进程生命周期内可执行文件的位置不会变**。
#: 用户在运行中装了驱动，重启应用才能看到 —— 这与「插件需重启加载」
#: 的既有约定一致，且远好于每次开下拉框卡 100ms。
#:
#: 需要强制刷新时（例如安装完驱动后在同一进程里复检）调
#: `clear_executable_cache()`。
_EXECUTABLE_CACHE: dict[str, Path | None] = {}


def clear_executable_cache() -> None:
    """清空可执行文件探测缓存（安装/卸载驱动后复检用）。"""
    _EXECUTABLE_CACHE.clear()


def _find_executable(name: str) -> Path | None:
    """在 PATH 里找可执行文件（结果按名称缓存，见 `_EXECUTABLE_CACHE`）。"""
    if name in _EXECUTABLE_CACHE:
        return _EXECUTABLE_CACHE[name]
    import shutil

    found = shutil.which(name)
    resolved = Path(found) if found else None
    _EXECUTABLE_CACHE[name] = resolved
    return resolved


def _webdriver_available(driver: str) -> bool:
    """检查某个 WebDriver 是否真的存在。

    判据：Python 包 `selenium` 已安装 **且** 驱动可执行文件在 PATH 上。
    驱动名按平台带不带 `.exe` 都试（Windows 上 `shutil.which` 会自己补）。
    """
    if importlib.util.find_spec("selenium") is None:
        return False
    names = _WEBDRIVER_NAMES.get(driver)
    if not names:
        return False
    return any(_find_executable(n) is not None for n in names)


_PLAYWRIGHT_ENGINE_DIRS = {
    "chromium": ("chromium-", "chromium_headless_shell-"),
    "firefox": ("firefox-",),
    "webkit": ("webkit-",),
}


def _playwright_browser_root() -> Path | None:
    """定位 Playwright 的浏览器缓存目录（与 Playwright 自身同样的查找顺序）。"""
    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if override:
        candidate = Path(override)
        return candidate if candidate.is_dir() else None
    # 默认位置：Windows / macOS / Linux
    home = Path.home()
    for candidate in (
        home / "AppData" / "Local" / "ms-playwright",
        home / "Library" / "Caches" / "ms-playwright",
        home / ".cache" / "ms-playwright",
    ):
        if candidate.is_dir():
            return candidate
    return None


def _playwright_engine_available(engine: str) -> bool:
    """检查 Playwright 的**某个内核**是否真的可用。

    两道判据缺一不可：
    1. Python 包 `playwright` 已安装（否则连启动都做不到）；
    2. `ms-playwright/` 下有该内核的二进制目录。

    **找不到浏览器缓存目录时判 False**（fail-closed）：不能因为"看不出有没有"
    就报可用 —— 那会把「未安装」误报成「可用」。
    """
    if importlib.util.find_spec("playwright") is None:
        return False
    prefixes = _PLAYWRIGHT_ENGINE_DIRS.get(engine)
    if not prefixes:
        return False
    root = _playwright_browser_root()
    if root is None:
        return False
    try:
        entries = list(root.iterdir())
    except OSError:
        return False
    for entry in entries:
        if not entry.is_dir():
            continue
        if any(entry.name.startswith(prefix) for prefix in prefixes):
            return True
    return False


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
        - ``"ocr"`` (bare slot name)
        - ``"ocr.paddle"`` (dotted capability name ⇒ slot is the **domain prefix** ``ocr``)
        - ``{"slot": "ocr", "id": "custom-id", "display_name": "...", "priority": 130}``

        Also recognizes component ``runtime.engine`` for ``ocr-file-v1`` recipes so
        existing installed OCR components participate without manifest migration.

        ★ 2026-11-27 修：**点分名此前会被当成槽位名本身**。

          市场 16 个组件的 `provides` 全是 `ocr.<name>` 形态 ⇒ 旧实现建出 16 个叫
          `ocr.convert-markdown` / `ocr.paddle` / … 的槽位，**而消费方查的是 `ocr`**
          （`build_builtin_slot_registry` 建的就是 `"ocr"`）。

          后果：这些组件**一个都不会被看见** —— 不是报错，是"注册成功了但没人查得到"，
          正是本项目反复出现的「定义了没有生产者」的镜像：**有生产者，没有消费者对得上**。

          判据：点分名的**域前缀**就是槽位（`ocr.paddle` ⇒ 槽位 `ocr`）；
          `provider_id` 仍用组件自己的 id，`metadata["capability"]` 记完整能力名，
          这样"同一槽位里有多个 provider"这件事才成立，`slot.active()` 才有得选。
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
                    declared = item.strip()
                    # 点分名 ⇒ 槽位取**域前缀**；裸名 ⇒ 它本身就是槽位名。
                    slot_name = declared.split(".", 1)[0] if "." in declared else declared
                    provider_id = base_id
                    display_name = base_title
                    priority = int(raw.get("priority", 110))
                    # 完整能力名留在 metadata 里：一个槽位下可能有多个 provider，
                    # 消费方需要能分辨"这个 provider 提供的是哪个具体能力"。
                    capability_name = declared
                elif isinstance(item, Mapping) and str(item.get("slot", "")).strip():
                    # 字典形态是**扩展语法**（显式指定槽位/优先级），此时 slot 字段
                    # 就是槽位名，不做点分拆分 —— 那是它自己的语义，不是 `provides` 的。
                    slot_name = str(item["slot"]).strip()
                    provider_id = str(item.get("id") or base_id).strip()
                    display_name = str(item.get("display_name") or base_title).strip()
                    priority = int(item.get("priority", raw.get("priority", 110)))
                    capability_name = slot_name
                else:
                    continue

                metadata: dict[str, Any] = {"manifest_id": base_id}
                if capability_name != slot_name:
                    # 只有点分名才有"完整能力名 ≠ 槽位名"，记下来供消费方分辨。
                    metadata["capability"] = capability_name
                self.register(
                    CapabilityProvider(
                        provider_id=provider_id,
                        slot_name=slot_name,
                        source=source,
                        display_name=display_name,
                        priority=priority,
                        description=purpose,
                        metadata=metadata,
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

    # 2. Browser automation engine slot (多内核与随包预置/外部浏览器平等槽位)
    browser_slot = reg.ensure_slot("browser", "动态网页渲染与交互浏览器引擎")
    browser_slot.register(
        CapabilityProvider(
            provider_id="playwright",
            slot_name="browser",
            source="builtin",
            display_name="Playwright (Chromium 随包预置)",
            priority=120,
            availability_check=lambda: _playwright_engine_available("chromium"),
            description="默认浏览器自动化引擎，随包预置完整 Chromium，支持请求级拦截与保真渲染",
            metadata={"engine_family": "chromium", "bundled": True},
        )
    )
    browser_slot.register(
        CapabilityProvider(
            provider_id="playwright-firefox",
            slot_name="browser",
            source="builtin",
            display_name="Playwright (Firefox 内核)",
            priority=100,
            availability_check=lambda: _playwright_engine_available("firefox"),
            description="可选 Firefox 浏览器内核，支持独立 Gecko 协议栈渲染与登录态隔离",
            metadata={"engine_family": "firefox", "bundled": False},
        )
    )
    browser_slot.register(
        CapabilityProvider(
            provider_id="playwright-webkit",
            slot_name="browser",
            source="builtin",
            display_name="Playwright (WebKit 内核)",
            priority=90,
            availability_check=lambda: _playwright_engine_available("webkit"),
            description="可选 WebKit 渲染内核，支持 Safari 系列排版引擎模拟",
            metadata={"engine_family": "webkit", "bundled": False},
        )
    )
    browser_slot.register(
        CapabilityProvider(
            provider_id="selenium",
            slot_name="browser",
            source="builtin",
            display_name="Selenium WebDriver (ChromeDriver)",
            priority=85,
            availability_check=lambda: _webdriver_available("chromedriver"),
            description="备用 WebDriver 引擎（需配合 ChromeDriver 运行）",
            metadata={"engine_family": "chromium", "bundled": False},
        )
    )
    browser_slot.register(
        CapabilityProvider(
            provider_id="selenium-firefox",
            slot_name="browser",
            source="builtin",
            display_name="Selenium WebDriver (GeckoDriver)",
            priority=80,
            availability_check=lambda: _webdriver_available("geckodriver"),
            description="备用 Firefox WebDriver 引擎（需配合 GeckoDriver 运行）",
            metadata={"engine_family": "firefox", "bundled": False},
        )
    )
    browser_slot.register(
        CapabilityProvider(
            provider_id="system-browser",
            slot_name="browser",
            source="builtin",
            display_name="本机自有浏览器 (可执行路径)",
            priority=75,
            availability_check=_spec_available("playwright"),
            description="连接用户本机已安装的 Edge / Chrome / Firefox 可执行文件",
            metadata={"engine_family": "custom", "bundled": False},
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
