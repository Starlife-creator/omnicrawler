"""配置校验器模块。

提供配置的 Schema 校验、选择器格式校验和框架兼容性检查。
"""

from __future__ import annotations

import re

from .config_model import CrawlConfig, FieldDef

# 框架支持的 source.kind 值
VALID_SOURCE_KINDS: set[str] = {
    "static_html", "crawl", "focused", "incremental", "url_list", "rest",
    "graphql", "form", "sitemap", "feed", "browser", "file", "media",
    "websocket", "sse", "long_poll", "redis", "scrapy",
}

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
        errors.append(f"字段 '{field.name}': 选择器为空")
        return errors

    if field.selector_type == "css":
        # 快速检查：CSS 选择器不能包含明显的非 CSS 结构
        if selector.startswith("$.") or selector.startswith("$["):
            errors.append(f"字段 '{field.name}': 选择器看起来像 JSONPath，但类型设为 CSS")
        if selector.startswith("//"):
            errors.append(f"字段 '{field.name}': 选择器看起来像 XPath，但类型设为 CSS")

    elif field.selector_type == "xpath":
        # 快速检查：XPath 通常以 / 或 // 开头，或以标签名开头
        if selector.startswith("$.") or selector.startswith("$["):
            errors.append(f"字段 '{field.name}': 选择器看起来像 JSONPath，但类型设为 XPath")
        if selector.startswith(".") or selector.startswith("#"):  # noqa: E127
            errors.append(f"字段 '{field.name}': 选择器看起来像 CSS，但类型设为 XPath （提示: XPath 以 // 开头）")

    elif field.selector_type == "jsonpath":
        if not selector.startswith("$"):
            errors.append(f"字段 '{field.name}': JSONPath 应以 $ 开头")

    return errors


def validate_schema(config_dict: dict) -> tuple[list[str], list[str]]:
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
            errors.append(f"未知的顶层配置项: '{key}'，允许的项: {', '.join(sorted(ALLOWED_TOP_KEYS))}")

    # 检查 project
    project = config_dict.get("project", {})
    if not isinstance(project, dict):
        errors.append("'project' 必须是映射")
    else:
        if "name" not in project:
            errors.append("'project.name' 是必填项")
        if "workspace" not in project:
            warnings.append("建议设置 'project.workspace'")

    # 检查 source
    source = config_dict.get("source", {})
    if not isinstance(source, dict):
        errors.append("'source' 必须是映射")
    else:
        if "kind" not in source:
            errors.append("'source.kind' 是必填项")
        elif source["kind"] not in VALID_SOURCE_KINDS:
            errors.append(f"不支持的 source.kind: '{source['kind']}'，支持的值: {', '.join(sorted(VALID_SOURCE_KINDS))}")
        if "seeds" not in source:
            errors.append("'source.seeds' 是必填项")
        elif not isinstance(source.get("seeds"), list):
            errors.append("'source.seeds' 必须是数组")
        elif len(source.get("seeds", [])) == 0 and source.get("kind") not in {"redis", "scrapy"}:
            errors.append("'source.seeds' 不能为空数组")

    # 检查 extract
    extract = config_dict.get("extract", {})
    if isinstance(extract, dict):
        fields = extract.get("fields", {})
        if isinstance(fields, dict) and len(fields) == 0:
            warnings.append("未定义精确字段，将由内核自动提取网址、标题、标题层级和正文")
    else:
        warnings.append("未定义 'extract' 配置段")

    return errors, warnings


def validate_full_config(config: CrawlConfig) -> tuple[list[str], list[str]]:
    """执行完整配置校验。

    包含 CrawlConfig.validate() 内置校验 + 选择器格式校验 + Schema 校验。

    Args:
        config: 爬虫配置对象。

    Returns:
        (errors, warnings) 元组。
    """
    errors = config.validate()
    warnings: list[str] = []

    # 选择器格式校验
    for field in config.fields:
        selector_errors = validate_selector_format(field)
        errors.extend(selector_errors)

    # source_kind 校验
    if config.source_kind not in VALID_SOURCE_KINDS:
        errors.append(f"不支持的网站类型: {config.source_kind}")

    # 检查占位符
    if config.has_placeholders():
        warnings.append("配置中存在未替换的模板占位符 {{...}}，请在运行前替换为真实值")

    return errors, warnings
