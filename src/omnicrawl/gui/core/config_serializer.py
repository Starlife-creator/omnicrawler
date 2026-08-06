"""配置序列化模块。

实现 CrawlConfig ↔ YAML 的双向转换，支持版本标记和健壮的错误处理。
"""

from __future__ import annotations

import copy
import re
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any

import ruamel.yaml
from ruamel.yaml.comments import CommentedMap

from ... import __version__ as GUI_VERSION  # noqa: N812
from ...core.secrets_store import SecretsStore
from ..i18n import _
from .config_model import CrawlConfig, DownloadConfig, FieldDef


def _seal_ai_provider_keys(providers: dict) -> None:
    """S2.2.2：明文 API key 密封进 secrets_store，替换为 secret:// 引用。

    已是引用（secret:// 前缀）原样保留；密封失败（无 keyring 且无主密码）
    抛可读 ValueError，绝不回退写明文。
    """
    store = SecretsStore()
    for name, provider in providers.items():
        if not isinstance(provider, dict):
            continue
        key = provider.get("api_key")
        if not key or str(key).startswith("secret://"):
            continue
        try:
            store.set(f"ai.{name}.api_key", str(key))
        except Exception as exc:  # noqa: BLE001 - 统一包装为可读错误
            raise ValueError(_(f"无法安全保存 API key（{name}）: {exc}")) from exc
        provider["api_key"] = f"secret://ai.{name}.api_key"


def _create_yaml_handler() -> ruamel.yaml.YAML:
    """创建配置好的 ruamel.yaml 处理器。"""
    yaml = ruamel.yaml.YAML(typ="rt")
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.preserve_quotes = True
    yaml.width = 4096
    yaml.allow_unicode = True
    return yaml


def to_yaml(config: CrawlConfig) -> str:
    """将 CrawlConfig 序列化为 YAML 字符串。

    Args:
        config: 爬虫配置对象。

    Returns:
        格式化的 YAML 字符串，顶部包含版本标记注释。
    """
    yaml_handler = _create_yaml_handler()

    # 构建嵌套字典
    root = CommentedMap()

    # project
    project = CommentedMap()
    project["name"] = config.project_name
    project["workspace"] = config.workspace
    project["intent"] = config.task_intent
    if config.task_description.strip():
        project["description"] = config.task_description.strip()
    root["project"] = project

    # source
    source = CommentedMap()
    source["kind"] = "feed" if config.source_kind == "rss" else config.source_kind
    source["seeds"] = config.seed_urls
    if config.pagination:
        source["pagination"] = config.pagination
    root["source"] = source

    # crawl
    crawl = CommentedMap()
    crawl["max_pages"] = config.max_pages
    crawl["concurrency"] = config.concurrency
    root["crawl"] = crawl

    # http
    http = CommentedMap()
    http["user_agent"] = config.user_agent
    http["respect_robots"] = config.respect_robots
    http["delay_seconds"] = config.delay
    http["auto_browser_fallback"] = True
    root["http"] = http

    # extract
    extract = CommentedMap()
    extract["mode"] = "html"
    extract["item_selector"] = ""
    fields_map = CommentedMap()
    for f in config.fields:
        field_value = CommentedMap()
        field_value["selector"] = f.selector
        if f.fallback_xpath:
            field_value["selectors"] = [
                {"selector": f.selector},
                {"xpath": f.fallback_xpath},
            ]
        if f.selector_type != "css":
            field_value["type"] = f.selector_type
        if f.attribute:
            field_value["attr"] = f.attribute
        if f.regex:
            field_value["regex"] = f.regex
        if f.required:
            field_value["required"] = True
        fields_map[f.name] = field_value
    extract["fields"] = fields_map
    root["extract"] = extract

    # download
    download = CommentedMap()
    download["enabled"] = config.download.enabled
    if config.download.enabled:
        download["extensions"] = config.download.extensions
        download["output_dir"] = config.download.output_dir
    root["download"] = download

    topic = CommentedMap()
    topic["enabled"] = bool(config.topic_include_any or config.topic_include_all or config.topic_exclude)
    topic["include_any"] = config.topic_include_any
    topic["include_all"] = config.topic_include_all
    topic["exclude"] = config.topic_exclude
    topic["match_on"] = ["url", "anchor", "title", "heading", "text"]
    topic["keep_uncertain"] = config.keep_uncertain_topics
    topic["filter_records"] = True
    root["selection"] = CommentedMap({"topic": topic})

    root["processors"] = CommentedMap({
        "pdf": CommentedMap({
            "enabled": config.process_pdf,
            "config": "builtin:pdf/generic_template.yaml",
            "skip_ocr": config.pdf_ocr == "never",
            "ocr_backend": config.pdf_ocr if config.pdf_ocr in {"paddle", "tesseract"} else "none",
        })
    })

    root["updates"] = CommentedMap({
        "enabled": config.monitor_same_url,
        "revisit_completed": config.monitor_same_url,
        "detect_same_url_changes": True,
        "keep_versions": True,
    })

    # incremental (仅在启用时写入)
    if config.incremental:
        incremental = CommentedMap()
        incremental["skip_unchanged"] = True
        if config.since_date:
            incremental["since_date"] = config.since_date
        root["incremental"] = incremental

    # outputs
    outputs = CommentedMap()
    for name in ("jsonl", "csv", "xlsx", "parquet", "duckdb"):
        outputs[name] = name in config.output_formats
    root["outputs"] = outputs

    providers = CommentedMap()
    if config.ai_mode != "disabled" and config.ai_provider:
        provider = CommentedMap({
            "type": "openai_compatible",
            "base_url": config.ai_base_url,
            "model": config.ai_model,
        })
        if config.ai_api_key_ref:
            provider["api_key"] = config.ai_api_key_ref
        providers[config.ai_provider] = provider
    # S2.2.2：AI api_key 出口加密——明文密封进 secrets_store，YAML 只写 secret:// 引用
    _seal_ai_provider_keys(providers)
    root["ai"] = CommentedMap({
        "mode": config.ai_mode,
        "default_provider": config.ai_provider,
        "providers": providers,
        "routing": CommentedMap(),
        "fallback": ["deterministic"],
        "extraction": CommentedMap({
            "mode": config.extraction_mode,
            "prompt": config.ai_extraction_prompt or "",
            "chunk_strategy": config.ai_chunk_strategy,
            "max_tokens_per_chunk": config.ai_max_tokens_per_chunk,
        }),
    })

    resources = CommentedMap()
    resources["profile"] = config.resource_profile
    root["resources"] = resources

    if config.passthrough:
        root = CommentedMap(_deep_overlay(copy.deepcopy(config.passthrough), root))

    # 序列化
    stream = StringIO()
    yaml_handler.dump(root, stream)
    yaml_str = stream.getvalue()

    # 添加头部注释
    header = (
        f"# OmniCrawler GUI v{GUI_VERSION} - "
        + _(f"生成于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        + f"# task_id: {config.task_id}\n"
    )
    return header + yaml_str


def from_yaml(yaml_str: str) -> CrawlConfig:
    """从 YAML 字符串反序列化为 CrawlConfig。

    Args:
        yaml_str: YAML 格式的配置字符串。

    Returns:
        CrawlConfig 实例。

    Raises:
        ValueError: YAML 格式错误或必填字段缺失时抛出。
    """
    yaml_handler = _create_yaml_handler()

    # 移除外层可能的 BOM
    yaml_str = yaml_str.lstrip("\ufeff")

    try:
        raw = yaml_handler.load(yaml_str)
    except Exception as e:
        raise ValueError(_(f"YAML 解析失败: {e}")) from e

    if raw is None:
        raise ValueError(_("YAML 内容为空"))

    if not isinstance(raw, dict):
        raise ValueError(_("YAML 顶层必须是映射 (mapping)"))

    config = CrawlConfig()
    config.passthrough = copy.deepcopy(dict(raw))

    # ---- project ----
    project = raw.get("project", {})
    if isinstance(project, dict):
        config.project_name = str(project.get("name", config.project_name))
        config.workspace = str(project.get("workspace", config.workspace))
        config.task_intent = str(project.get("intent", config.task_intent))
        description = project.get("description")
        if description is not None:
            config.task_description = str(description)

    # ---- source ----
    source = raw.get("source", {})
    if isinstance(source, dict):
        kind = source.get("kind", "static_html")
        if isinstance(kind, str):
            config.source_kind = ("feed" if kind == "rss" else kind)
        seeds = source.get("seeds", [])
        if isinstance(seeds, list):
            config.seed_urls = [str(s) for s in seeds if s is not None]
        pagination = source.get("pagination")
        if isinstance(pagination, dict):
            config.pagination = dict(pagination)

    # ---- crawl ----
    crawl = raw.get("crawl", {})
    if isinstance(crawl, dict):
        config.max_pages = int(crawl.get("max_pages", config.max_pages))
        config.delay = float(crawl.get("delay_seconds", config.delay))
        config.concurrency = int(crawl.get("concurrency", config.concurrency))
        pagination = crawl.get("pagination")
        if isinstance(pagination, dict) and config.pagination is None:
            legacy = dict(pagination)
            if "param" in legacy and "parameter" not in legacy:
                legacy["parameter"] = legacy.pop("param")
            legacy.setdefault("type", "page")
            config.pagination = legacy

    # ---- http ----
    http = raw.get("http", {})
    if isinstance(http, dict):
        config.user_agent = str(http.get("user_agent", config.user_agent))
        config.respect_robots = bool(http.get("respect_robots", config.respect_robots))
        config.delay = float(http.get("delay_seconds", config.delay))

    # ---- extract ----
    extract = raw.get("extract", {})
    fields_list: list[FieldDef] = []
    if isinstance(extract, dict):
        fields = extract.get("fields", {})
        if isinstance(fields, dict):
            for field_name, field_spec in fields.items():
                if isinstance(field_spec, dict):
                    selector = str(field_spec.get("selector", ""))
                    selector_type = str(field_spec.get("type", "css"))
                    if selector_type not in ("css", "xpath", "jsonpath"):
                        selector_type = "css"
                    attr = field_spec.get("attr")
                    regex = field_spec.get("regex")
                    fallback_xpath = ""
                    selector_candidates = field_spec.get("selectors", [])
                    if isinstance(selector_candidates, list):
                        for candidate in selector_candidates:
                            if isinstance(candidate, dict) and candidate.get("xpath"):
                                fallback_xpath = str(candidate["xpath"])
                                break
                    fields_list.append(FieldDef(
                        name=str(field_name),
                        selector=selector,
                        selector_type=selector_type,  # type: ignore[arg-type]
                        attribute=str(attr) if attr else None,
                        regex=str(regex) if regex else None,
                        required=bool(field_spec.get("required", False)),
                        fallback_xpath=fallback_xpath or None,
                    ))
    config.fields = fields_list

    selection = raw.get("selection", {})
    topic = selection.get("topic", {}) if isinstance(selection, dict) else {}
    if isinstance(topic, dict):
        config.topic_include_any = [str(item) for item in topic.get("include_any", [])]
        config.topic_include_all = [str(item) for item in topic.get("include_all", [])]
        config.topic_exclude = [str(item) for item in topic.get("exclude", [])]
        config.keep_uncertain_topics = bool(topic.get("keep_uncertain", True))

    # ---- download ----
    download = raw.get("download", {})
    if isinstance(download, dict):
        enabled = bool(download.get("enabled", False))
        extensions = download.get("extensions", [])
        if isinstance(extensions, list):
            ext_list = [str(e) for e in extensions]
        else:
            ext_list = [".pdf", ".jpg"]
        output_dir = str(download.get("output_dir", "downloads"))
        config.download = DownloadConfig(
            enabled=enabled,
            extensions=ext_list,
            output_dir=output_dir,
        )

    # ---- incremental ----
    incremental = raw.get("incremental", {})
    if isinstance(incremental, dict):
        config.incremental = bool(incremental.get("skip_unchanged", False))
        since = incremental.get("since_date")
        if since:
            config.since_date = str(since)

    processors = raw.get("processors", {})
    pdf = processors.get("pdf", {}) if isinstance(processors, dict) else {}
    if isinstance(pdf, dict):
        config.process_pdf = bool(pdf.get("enabled", False))
        backend = str(pdf.get("ocr_backend", "none")).casefold()
        config.pdf_ocr = "never" if pdf.get("skip_ocr", False) else backend if backend in {"paddle", "tesseract"} else "auto"

    updates = raw.get("updates", {})
    if isinstance(updates, dict):
        config.monitor_same_url = bool(updates.get("enabled", False))

    outputs = raw.get("outputs", {})
    if isinstance(outputs, dict):
        selected = [name for name in ("jsonl", "csv", "xlsx", "parquet", "duckdb") if outputs.get(name, False)]
        if selected:
            config.output_formats = selected

    ai = raw.get("ai", {})
    if isinstance(ai, dict):
        config.ai_mode = str(ai.get("mode", "disabled"))
        config.ai_provider = str(ai.get("default_provider", ""))
        providers = ai.get("providers", {})
        provider = providers.get(config.ai_provider, {}) if isinstance(providers, dict) else {}
        if isinstance(provider, dict):
            config.ai_base_url = str(provider.get("base_url", ""))
            config.ai_model = str(provider.get("model", ""))
            config.ai_api_key_ref = str(provider.get("api_key", ""))
        extraction = ai.get("extraction", {})
        if isinstance(extraction, dict):
            mode = str(extraction.get("mode", "selector")).casefold()
            if mode in ("selector", "ai", "hybrid"):
                config.extraction_mode = mode  # type: ignore[assignment]
            config.ai_extraction_prompt = str(extraction.get("prompt") or "") or None
            config.ai_chunk_strategy = str(extraction.get("chunk_strategy", "auto"))
            try:
                config.ai_max_tokens_per_chunk = int(extraction.get("max_tokens_per_chunk", 4000))
            except (TypeError, ValueError):
                pass

    resources = raw.get("resources", {})
    if isinstance(resources, dict):
        profile = str(resources.get("profile", "balanced")).casefold()
        if profile in ("economy", "balanced", "performance"):
            config.resource_profile = profile  # type: ignore[assignment]

    # ---- 尝试从注释中提取 task_id ----
    if hasattr(raw, "ca") and raw.ca and raw.ca.comment:
        comment_text = "\n".join(str(c) for c in (raw.ca.comment[1] if len(raw.ca.comment) > 1 else []) if c)
        match = re.search(r"task_id:\s*([a-f0-9-]+)", comment_text or "")
        if match:
            config.task_id = match.group(1)

    return config


def save_yaml(config: CrawlConfig, filepath: Path) -> None:
    """将配置保存为 YAML 文件。

    Args:
        config: 爬虫配置对象。
        filepath: 目标文件路径。
    """
    filepath.parent.mkdir(parents=True, exist_ok=True)
    yaml_str = to_yaml(config)
    filepath.write_text(yaml_str, encoding="utf-8")


def load_yaml(filepath: Path) -> CrawlConfig:
    """从 YAML 文件加载配置。

    Args:
        filepath: YAML 文件路径。

    Returns:
        CrawlConfig 实例。

    Raises:
        FileNotFoundError: 文件不存在时抛出。
        ValueError: 文件格式或内容无效时抛出。
    """
    if not filepath.is_file():
        raise FileNotFoundError(_(f"配置文件不存在: {filepath}"))
    try:
        yaml_str = filepath.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(_(f"文件编码错误，请使用 UTF-8 编码: {e}")) from e
    return from_yaml(yaml_str)


def format_yaml(yaml_str: str) -> str:
    """格式化 YAML 字符串（保留注释）。

    Args:
        yaml_str: 原始 YAML 字符串。

    Returns:
        格式化后的 YAML 字符串。
    """
    yaml_handler = _create_yaml_handler()
    try:
        data = yaml_handler.load(yaml_str)
    except Exception:
        return yaml_str
    stream = StringIO()
    yaml_handler.dump(data, stream)
    return stream.getvalue()


def _deep_overlay(base: Any, overlay: Any) -> Any:
    if isinstance(base, dict) and isinstance(overlay, dict):
        result = copy.deepcopy(base)
        for key, value in overlay.items():
            result[key] = _deep_overlay(result[key], value) if key in result else copy.deepcopy(value)
        return result
    return copy.deepcopy(overlay)
