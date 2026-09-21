"""配置校验器模块。

提供配置的 Schema 校验、选择器格式校验和框架兼容性检查。
"""

from __future__ import annotations

import re
from pathlib import Path

from ...core.field_value_source import (
    COMMON_ATTRIBUTES,
    POSITION_CHILD,
    POSITION_ELEMENT_ATTR,
)
from ...sources.sources import SUPPORTED_SOURCE_KINDS as VALID_SOURCE_KINDS
from ..i18n import _
from .config_model import CrawlConfig, FieldDef

# 框架必需的顶层 key
REQUIRED_TOP_KEYS: set[str] = {
    "project", "source", "crawl", "http", "extract",
}

# 框架允许的顶层 key
ALLOWED_TOP_KEYS: set[str] = {
    "project", "source", "crawl", "http", "extract", "download",
    "incremental", "processors", "outputs", "plugins", "browser",
    "config_version", "task", "selection", "updates", "ai", "auth",
    "session", "data_quality", "transformers", "regression", "storage",
    "resources", "api_discovery",
}

# CSS 选择器语法基本检查
CSS_SELECTOR_PATTERN = re.compile(
    r"^(?:[#.]?[a-zA-Z_][\w-]*|\[.*?\]|\*|::?(?:after|before|first-letter|first-line"
    r"|selection|marker|placeholder|nth-child|nth-of-type|not|has|is|where)"
    r")(?:[\s>+~].*)?$"
)

# XPath 快速检查模式
XPATH_PATTERN = re.compile(r"^(?:/?/|\.?\./)?[\w*@\[\(].*$", re.IGNORECASE)

# JSONPath 快速检查模式
JSONPATH_PATTERN = re.compile(r"^\$[.[].*$", re.IGNORECASE)


def validate_selector_format(field: FieldDef) -> list[str]:
    """校验选择器格式的基本合法性。

    Args:
        field: 字段定义。

    Returns:
        错误信息列表。
    """
    errors: list[str] = []
    selector = field.selector.strip()

    if not selector:
        # 2026-09-15：选择器为空是否合法**由取值位置契约判定**（core/field_value_source.py）——
        # 「取条目元素自身（文本/属性）」本来就允许空选择器，引擎也支持（extractors.py 里
        # `select_nodes(context, selector) if selector else [context]`）。此前这里一律报
        # 「选择器为空」，导致「取本条记录自己的 href」这种规则在表单里建不出来。
        #
        # 位置**未声明**且选择器为空 ⇒ 仍报错：不能把"忘填选择器"静默解释成"取元素自身"。
        position = field.position or None
        if position is None:
            errors.append(_(f"字段 '{field.name}': 选择器为空（要取条目元素自身请先选取值位置）"))
        elif position == POSITION_CHILD:
            errors.append(_(f"字段 '{field.name}': 选择器为空"))
        elif position == POSITION_ELEMENT_ATTR and not (field.attribute or "").strip():
            errors.append(_(f"字段 '{field.name}': 取元素自身属性时必须填属性名"))
        return errors

    # 反向检查（契约同款）：把属性名写进选择器 —— 引擎会把它当 CSS 选择器去找，**静默取不到值**
    if field.selector_type == "css" and selector in COMMON_ATTRIBUTES:
        errors.append(
            _(
                f"字段 '{field.name}': 选择器 {selector!r} 看起来是属性名；"
                "要取条目元素自身的属性，请把选择器留空并填写属性名"
            )
        )
        return errors

    if field.selector_type == "css":
        # 快速检查：CSS 选择器不能包含明显的非 CSS 结构
        if selector.startswith("$.") or selector.startswith("$["):
            errors.append(_(f"字段 '{field.name}': 选择器看起来像 JSONPath，但类型设为 CSS"))
        if selector.startswith("//"):
            errors.append(_(f"字段 '{field.name}': 选择器看起来像 XPath，但类型设为 CSS"))

    elif field.selector_type == "xpath":
        # 快速检查：XPath 通常以 / 或 // 开头，或以标签名开头
        if selector.startswith("$.") or selector.startswith("$["):
            errors.append(_(f"字段 '{field.name}': 选择器看起来像 JSONPath，但类型设为 XPath"))
        if selector.startswith(".") or selector.startswith("#"):  # noqa: E127
            errors.append(_(f"字段 '{field.name}': 选择器看起来像 CSS，但类型设为 XPath （提示: XPath 以 // 开头）"))

    elif field.selector_type == "jsonpath":
        if not selector.startswith("$"):
            errors.append(_(f"字段 '{field.name}': JSONPath 应以 $ 开头"))
        else:
            from ...core.jsonpath import JsonPathSyntaxError, compile_path

            try:
                compile_path(selector)
            except JsonPathSyntaxError as exc:
                errors.append(_(f"字段 '{field.name}': JSONPath 语法错误: {exc}"))

    return errors


def plugin_source_kinds(project_root: str | Path | None = None) -> set[str]:
    """D10-b：从项目根构建插件 registry，返回已注册的 source.kind 集合。

    GUI 校验 source.kind 时并入这些类型，避免误拒插件注册的动态源。
    registry 构建失败（插件损坏/无插件目录）时返回空集，不影响内置校验。
    """
    from ...core.config import DEFAULTS, AppConfig, deep_merge

    try:
        raw = deep_merge({}, DEFAULTS)
        raw["project"] = {"name": "validator", "workspace": "work"}
        root = Path(project_root).expanduser() if project_root else Path.cwd()
        config = AppConfig(Path("<validator>"), root, raw, root)
        from ...pipeline import build_registry

        registry = build_registry(config)
        return set(registry.sources)
    except Exception:  # noqa: BLE001 - 插件问题不阻塞校验
        return set()


def validate_schema(
    config_dict: dict,
    *,
    extra_source_kinds: set[str] | None = None,
) -> tuple[list[str], list[str]]:
    """对配置字典进行 Schema 白名单检查。

    Args:
        config_dict: 配置字典。

    Returns:
        (errors, warnings) 元组。
    """
    errors: list[str] = []
    warnings: list[str] = []

    # 检查顶层 key
    for key in config_dict:
        if key not in ALLOWED_TOP_KEYS:
            errors.append(_(f"未知的顶层配置项: '{key}'，允许的项: {', '.join(sorted(ALLOWED_TOP_KEYS))}"))

    # 检查 project
    project = config_dict.get("project", {})
    if not isinstance(project, dict):
        errors.append(_("'project' 必须是映射"))
    else:
        if "name" not in project:
            errors.append(_("'project.name' 是必填项"))
        if "workspace" not in project:
            warnings.append(_("建议设置 'project.workspace'"))

    # 检查 source
    source = config_dict.get("source", {})
    valid_kinds = set(VALID_SOURCE_KINDS) | (extra_source_kinds or set())
    if not isinstance(source, dict):
        errors.append(_("'source' 必须是映射"))
    else:
        if "kind" not in source:
            errors.append(_("'source.kind' 是必填项"))
        elif source["kind"] not in valid_kinds:
            errors.append(_(f"不支持的 source.kind: '{source['kind']}'，支持的值: {', '.join(sorted(valid_kinds))}"))
        # 插件注册的动态源（不在内置白名单）不强制 seeds：其入口常为输入文件（见 #75）。
        plugin_kind = isinstance(source.get("kind"), str) and source.get("kind") not in VALID_SOURCE_KINDS
        if not plugin_kind:
            if "seeds" not in source:
                errors.append(_("'source.seeds' 是必填项"))
            elif not isinstance(source.get("seeds"), list):
                errors.append(_("'source.seeds' 必须是数组"))
            elif len(source.get("seeds", [])) == 0 and source.get("kind") not in {"redis", "scrapy"}:
                errors.append(_("'source.seeds' 不能为空数组"))

    # 检查 extract
    extract = config_dict.get("extract", {})
    if isinstance(extract, dict):
        fields = extract.get("fields", {})
        if isinstance(fields, dict) and len(fields) == 0:
            warnings.append(_("未定义精确字段，将由内核自动提取网址、标题、标题层级和正文"))
    else:
        warnings.append(_("未定义 'extract' 配置段"))

    return errors, warnings


def validate_full_config(
    config: CrawlConfig,
    *,
    extra_source_kinds: set[str] | None = None,
) -> tuple[list[str], list[str]]:
    """执行完整配置校验。

    包含 CrawlConfig.validate() 内置校验 + 选择器格式校验 + Schema 校验。

    Args:
        config: 爬虫配置对象。

    Returns:
        (errors, warnings) 元组。
    """
    # 插件文件型源允许无种子：仅内置 source.kind 要求种子 URL（见 #75）。
    errors = config.validate(require_seeds=config.source_kind in VALID_SOURCE_KINDS)
    warnings: list[str] = []

    # 选择器格式校验（JSON 模式的字段契约是 path / paths，不适用选择器规则）
    if config.extract_mode() != "json":
        for field in config.fields:
            selector_errors = validate_selector_format(field)
            errors.extend(selector_errors)

    # source_kind 校验
    valid_kinds = set(VALID_SOURCE_KINDS) | (extra_source_kinds or set())
    if config.source_kind not in valid_kinds:
        errors.append(_(f"不支持的网站类型: {config.source_kind}"))

    # 检查占位符
    if config.has_placeholders():
        warnings.append(_("配置中存在未替换的模板占位符 {{...}}，请在运行前替换为真实值"))

    # S2.1.1 ④：转调核心 validate_config（单一校验真源）
    _core_errors, _core_warnings = _core_validate(config)
    errors.extend(_core_errors)
    warnings.extend(_core_warnings)

    return errors, warnings


def _core_validate(config: CrawlConfig) -> tuple[list[str], list[str]]:
    """把 GUI 配置的 passthrough 原始键经核心 validate_config 校验。

    无种子 URL 时跳过（核心在无 seeds 下校验无意义）。
    """
    from pathlib import Path

    from ...core.config import DEFAULTS, AppConfig, deep_merge, validate_config

    try:
        raw = deep_merge(DEFAULTS, dict(getattr(config, "passthrough", {}) or {}))
        raw.setdefault("project", {
            "name": config.project_name or "gui",
            "workspace": config.workspace or "work",
        })
        # CrawlConfig 独立字段（不在 passthrough）同步进核心原始配置
        raw.setdefault("http", {})
        if getattr(config, "user_agent", ""):
            raw["http"]["user_agent"] = config.user_agent
        if not (raw.get("source", {}) or {}).get("seeds"):
            return [], []
        core = AppConfig(Path("<gui>"), Path.cwd(), raw, Path.cwd())
        return validate_config(core)
    except Exception:  # noqa: BLE001 - 核心校验失败不阻断 GUI 校验
        return [], []
