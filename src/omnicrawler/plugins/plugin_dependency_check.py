"""插件声明依赖的可用性预检（issue #75 §D）。

插件把第三方依赖声明在 ``PLUGIN_METADATA["dependencies"]``（``{name, version, license}``）。
缺依赖时插件往往在**运行到一半**才 ``ImportError``——更糟的是静默降级成"假成功"
（见 OmniCrawler-market#22 的 Cloudflare 页面被当成论文）。这里在运行前把声明依赖转成
预检检查项，给出可操作的提示。

**为什么是 warning 而不是 error**：manifest 声明的是插件的**依赖全集**，不是"本次配置
一定用到"的子集——实测 `academic-paper-downloader` 声明 6 个依赖，其中 ``playwright``
只在 level 3（浏览器/校园 IP）才需要，``pypdf`` 只用于结果核验。一律判 error 会把
"能跑通的 run"挡在门外，反而让人学会忽略预检。⇒ 报 warning + 明确清单，
既不伪成功，也不误阻。

只检查**会参与本次运行**的插件（``source.kind`` 提供者 + ``plugins.enabled_market_plugins``）：
把"装了但没启用"的插件的缺依赖也报出来是噪音。

## 第二条线：组件能力（2026-11-27）

与「第三方依赖」平行、但**不是同一件事**：

| | 依赖 | 组件能力 |
|---|---|---|
| 声明字段 | ``dependencies`` | ``requires_components`` |
| 缺失后果 | 运行到一半 ``ImportError`` | 运行到一半 ``ModuleNotFoundError`` / 组件调用失败 |
| 满足方式 | ``pip install X`` | 在市场里装一个组件 |

``plugin_component_requirements()`` 把「插件要 ``ocr.paddle``、但本机没装提供它的
组件」变成运行前的一条 warning + 「装哪个」的具体提示。

★ 为什么必须在预检里做，而不是只在市场侧：``component_capability.check_market()``
  管的是「**发布时**有没有组件兜底」，而「**用户机器上装没装**」只有预检知道。
  这条正是「函数写好了但零调用方」的那个洞 —— ``install_hint()`` 此前没有任何调用者。
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from .plugin_preflight import _decode_plugin_source, _static_plugin_metadata

__all__ = [
    "DependencyStatus",
    "import_name_candidates",
    "plugin_component_requirements",
    "plugin_dependency_requirements",
    "plugin_dependency_status",
    "plugin_dependency_warnings",
]

ENTRY_FILE = "plugin.py"
MANIFEST_FILE = "plugin.yaml"
_MAX_SCAN_DEPTH = 3


def import_name_candidates(name: str) -> tuple[str, ...]:
    """把声明的依赖名归一成**可探测的模块名候选**。

    生态约定写 import 名（``httpx``/``yaml``/``openpyxl``），但分发名（``PyYAML``、
    ``scikit-learn``）也时有出现 ⇒ 返回多个候选，**全部探测不到**才算缺失（避免误报）。
    """
    raw = name.strip()
    if not raw:
        return ()
    candidates = [raw, raw.replace("-", "_")]
    if raw.isupper() or any(char.isupper() for char in raw):
        candidates.append(raw.lower())
    return tuple(dict.fromkeys(item for item in candidates if item))


def _normalized_dependency_names(dependencies: Any) -> list[str]:
    """从 ``dependencies`` 声明里取出依赖名（兼容 dict 条目与纯字符串条目）。"""
    if not isinstance(dependencies, (list, tuple)):
        return []
    names: list[str] = []
    for item in dependencies:
        value = item.get("name") if isinstance(item, dict) else item
        if isinstance(value, str) and value.strip():
            names.append(value.strip())
    return names


def _wanted_plugin_ids(config: Any) -> set[str]:
    """本次 run 会参与的插件 id：``source.kind``（若为插件）+ 已启用的市场插件。"""
    wanted: set[str] = set()
    source_kind = str(getattr(config, "source_kind", "") or "").strip()
    if source_kind:
        wanted.add(source_kind)
    plugins_cfg = config.section("plugins")
    enabled = plugins_cfg.get("enabled_market_plugins")
    if isinstance(enabled, (list, tuple)):
        wanted.update(str(item).strip() for item in enabled if str(item).strip())
    return wanted


def _plugin_roots(config: Any) -> list[Path]:
    """插件目录候选：配置声明的 ``plugins.paths`` + 市场安装目录 ``plugins_installed/``。"""
    roots: list[Path] = []
    plugins_cfg = config.section("plugins")
    for entry in plugins_cfg.get("paths", []) or []:
        text = str(entry).strip()
        if not text:
            continue
        try:
            roots.append(config.resolve(text))
        except (OSError, ValueError):  # 路径异常不阻断预检
            continue
    try:
        roots.append(Path(config.root) / "plugins_installed")
    except (AttributeError, TypeError):
        pass
    return roots


def _iter_plugin_dirs(root: Path, *, max_depth: int = _MAX_SCAN_DEPTH) -> Iterator[Path]:
    """广度受限地找出插件目录（含 ``plugin.py`` 或 ``plugin.yaml`` 的目录）。"""
    if not root.is_dir():
        return
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        try:
            has_entry = (current / ENTRY_FILE).is_file()
            has_manifest = (current / MANIFEST_FILE).is_file()
        except OSError:  # 不可读目录跳过，不阻断
            continue
        if has_entry or has_manifest:
            yield current
            continue
        if depth >= max_depth:
            continue
        try:
            children = sorted(entry for entry in current.iterdir() if entry.is_dir())
        except OSError:
            continue
        stack.extend((child, depth + 1) for child in children if not child.name.startswith("."))


def _declared_dependencies(plugin_dir: Path) -> tuple[str, list[str]] | None:
    """读取插件声明：返回 ``(plugin_id, 依赖名列表)``；读不出则 ``None``。

    插件 id 取自 ``PLUGIN_METADATA["name"]``——与 ``plugin_loader`` 注册
    ``registry.sources[plugin_id]`` 时用的是同一个值，因此可与 ``source.kind`` 直接比对。
    """
    entry = plugin_dir / ENTRY_FILE
    if not entry.is_file():
        return None
    try:
        source = _decode_plugin_source(entry, entry.read_bytes())
        metadata = _static_plugin_metadata(entry, source)
    except (OSError, PermissionError, SyntaxError):
        # 插件自身的问题由加载路径报告；预检不越权判它，也不因此中断
        return None
    if metadata is None:
        return None
    return str(metadata.name), _normalized_dependency_names(metadata.dependencies)


def plugin_dependency_requirements(config: Any) -> list[tuple[str, tuple[str, ...], str]]:
    """本次 run 涉及插件中**缺失**的声明依赖。

    Returns:
        ``(插件 id, 模块名候选, 安装提示)`` 列表；无缺失时为空。
    """
    wanted = _wanted_plugin_ids(config)
    if not wanted:
        return []
    findings: list[tuple[str, tuple[str, ...], str]] = []
    seen: set[tuple[str, str]] = set()
    for plugin_dir in _iter_plugin_paths(config):
        declared = _declared_dependencies(plugin_dir)
        if declared is None:
            continue
        plugin_id, names = declared
        if plugin_id not in wanted:
            continue
        for name in names:
            candidates = import_name_candidates(name)
            if not candidates or any(find_spec(item) is not None for item in candidates):
                continue
            key = (plugin_id, name)
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                (
                    plugin_id,
                    candidates,
                    f"pip install {name}；便携版请改用 Full 版（Standard 不含可选依赖）",
                )
            )
    return findings


#: 已安装组件的清单（`installed.json`）相对工作区的路径。
#: 与 `services/workspace.py` 的 `component_manifest` 同源。
COMPONENT_MANIFEST_RELPATH = Path("components") / "installed.json"


def plugin_component_requirements(
    config: Any,
) -> list[tuple[str, tuple[str, ...], str]]:
    """本次 run 涉及插件中**缺失**的组件能力。

    与 `plugin_dependency_requirements` 是两条平行的线：

    | | 依赖（#75 §D）| 组件能力（本条）|
    |---|---|---|
    | 声明字段 | `dependencies` | `requires_components` |
    | 缺失后果 | 运行到一半 `ImportError` | 运行到一半 `ModuleNotFoundError` / 组件调用失败 |
    | 满足方式 | `pip install X` | 在市场里装一个组件 |

    ★ 为什么必须在这里而不是只在市场侧：`component_capability.check_market()`
      管的是「发布时有没有组件兜底」，而**用户机器上装没装**只有预检知道。
      两者缺一不可 —— 这条正是「写好了函数但零调用方」的那个洞。

    Returns:
        ``(插件 id, 缺失能力, 安装提示)`` 列表；无缺失时为空。
    """
    from ..services.component_capability import (
        install_hint,
        resolve,
    )

    wanted = _wanted_plugin_ids(config)
    if not wanted:
        return []

    plugins: list[dict[str, Any]] = []
    for plugin_dir in _iter_plugin_paths(config):
        declared = _declared_component_needs(plugin_dir)
        if declared is None:
            continue
        plugin_id, capabilities = declared
        if plugin_id not in wanted or not capabilities:
            continue
        plugins.append({"id": plugin_id, "requires_components": list(capabilities)})
    if not plugins:
        return []

    components = _installed_components(config)
    report = resolve(plugins, components)
    findings: list[tuple[str, tuple[str, ...], str]] = []
    for check in report.checks:
        if check.ok:
            continue
        findings.append((check.plugin_id, tuple(check.missing), install_hint(check, components)))
    return findings


def _installed_components(config: Any) -> list[dict[str, Any]]:
    """读工作区的 `components/installed.json`，折成 capability 模块要的形状。

    **读不到就返回空列表**（不是抛异常）：预检不该因为清单缺失而失败 ——
    清单不存在等价于「一个组件都没装」，那正好是要报的那个 warning。
    """
    import json

    try:
        workspace = Path(config.workspace)
    except (AttributeError, TypeError):
        return []
    manifest = workspace / COMPONENT_MANIFEST_RELPATH
    if not manifest.is_file():
        return []
    try:
        document = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(document, dict):
        return []
    entries: list[dict[str, Any]] = []
    for name, entry in document.items():
        if not isinstance(entry, dict):
            continue
        provides = entry.get("provides")
        entries.append(
            {"id": str(entry.get("name") or name), "provides": list(provides) if isinstance(provides, (list, tuple)) else []}
        )
    return entries


def _declared_component_needs(plugin_dir: Path) -> tuple[str, tuple[str, ...]] | None:
    """从插件目录读出 `(plugin_id, requires_components)`；读不到返回 None。

    与 `_declared_dependencies` 同套路：`plugin.py` 的 `PLUGIN_METADATA` 优先，
    其次 `plugin.yaml`。**只在两处都没有时返回 None** —— 那是「这个插件没有组件需求」
    的正常形态，不是错误。
    """
    metadata = _plugin_metadata(plugin_dir)
    if not isinstance(metadata, dict):
        return None
    plugin_id = str(metadata.get("name") or plugin_dir.name).strip()
    if not plugin_id:
        return None
    raw = metadata.get("requires_components")
    if isinstance(raw, str) or not isinstance(raw, (list, tuple)):
        return (plugin_id, ())
    capabilities = tuple(
        str(item).strip().lower() for item in raw if str(item).strip()
    )
    return (plugin_id, capabilities)


def _plugin_metadata(plugin_dir: Path) -> dict[str, Any] | None:
    """读插件元数据：`plugin.py` 的 `PLUGIN_METADATA` 优先，其次 `plugin.yaml`。"""
    entry = plugin_dir / ENTRY_FILE
    if entry.is_file():
        try:
            source = _decode_plugin_source(entry, entry.read_bytes())
            static = _static_plugin_metadata(entry, source)
        except (OSError, ValueError, SyntaxError):
            static = None
        if static is not None:
            return {
                "name": static.name,
                "requires_components": list(static.requires_components),
            }
    manifest = plugin_dir / MANIFEST_FILE
    if manifest.is_file():
        try:
            import yaml

            loaded = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if isinstance(loaded, dict):
            return loaded
    return None


def _iter_plugin_paths(config: Any) -> Iterable[Path]:
    for root in _plugin_roots(config):
        yield from _iter_plugin_dirs(root)


def plugin_dependency_warnings(config: Any) -> list[str]:
    """把缺失依赖渲染成可直接展示的一句话（GUI/CLI 共用同一措辞）。"""
    return [
        f"插件 {plugin_id} 声明的依赖未安装：{candidates[0]}（{hint}）"
        for plugin_id, candidates, hint in plugin_dependency_requirements(config)
    ]


@dataclass(frozen=True, slots=True)
class DependencyStatus:
    """单个插件的**只读**依赖状态（决策四：打开即检测，不冻结 UI、不弹框）。

    ``missing`` 为空即"依赖齐全"（§R2 ⇒ GUI 静默，不打扰用户）；非空时 GUI 只画
    徽标，把"是否安装"留给用户主动点击（决策四 A：只读）。
    """

    plugin_id: str
    declared: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    requirements: tuple[str, ...] = field(default=())

    @property
    def ready(self) -> bool:
        """依赖齐全（含"未声明依赖"这一平凡情形）。"""
        return not self.missing


def plugin_dependency_status_from_dir(plugin_dir: Path) -> DependencyStatus | None:
    """对**单个**插件目录做只读依赖探测；读不出声明时返回 ``None``。

    与 ``plugin_dependency_requirements`` 共用同一套探测口径（``import_name_candidates``
    + ``find_spec``），保证"运行前预检"与"打开即检测"给出**一致**的结论——
    两处判据不重复定义（判据只留一处）。
    """
    declared = _declared_dependencies(plugin_dir)
    if declared is None:
        return None
    plugin_id, names = declared
    missing: list[str] = []
    requirements: list[str] = []
    for name in names:
        candidates = import_name_candidates(name)
        if not candidates or any(find_spec(item) is not None for item in candidates):
            continue
        missing.append(candidates[0])
        requirements.append(name)
    return DependencyStatus(
        plugin_id=plugin_id,
        declared=tuple(names),
        missing=tuple(missing),
        requirements=tuple(requirements),
    )


def plugin_dependency_status(config: Any) -> dict[str, DependencyStatus]:
    """扫描**全部**已装插件（不限于本次 run 参与者）的依赖状态。

    决策四「打开即检测」的数据源：市场页只需展示**已安装**插件的依赖徽标，
    与"本次 run 是否启用"无关，因此这里不沿用 ``_wanted_plugin_ids`` 过滤。
    """
    result: dict[str, DependencyStatus] = {}
    for plugin_dir in _iter_plugin_paths(config):
        status = plugin_dependency_status_from_dir(plugin_dir)
        if status is None:
            continue
        # 同一 id 多次出现（多路径）时保留信息更全的一条（missing 更多者优先）
        existing = result.get(status.plugin_id)
        if existing is None or len(status.missing) > len(existing.missing):
            result[status.plugin_id] = status
    return result


def plugin_dependency_status_for_root(plugins_root: Path) -> dict[str, DependencyStatus]:
    """按**目录**（而非 AppConfig）扫描依赖状态——GUI 市场页只有安装根目录。

    与 ``plugin_dependency_status`` 同口径，只是把"路径枚举"换成显式根目录，
    供 plugin_market / dependency badge 在无 config 对象的场景复用。
    """
    result: dict[str, DependencyStatus] = {}
    if not plugins_root.is_dir():
        return result
    for plugin_dir in _iter_plugin_dirs(plugins_root):
        status = plugin_dependency_status_from_dir(plugin_dir)
        if status is None:
            continue
        existing = result.get(status.plugin_id)
        if existing is None or len(status.missing) > len(existing.missing):
            result[status.plugin_id] = status
    return result
