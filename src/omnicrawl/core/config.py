from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..pdfx.templates import DEFAULT_PDF_TEMPLATE, LEGACY_DEFAULT_PDF_TEMPLATE, resolve_builtin_pdf_reference
from .credentials import resolve_secret_refs
from .migrations import CURRENT_CONFIG_VERSION, migrate_config
from .utils import deep_merge, expand_env, user_agent

SOURCE_KINDS = {
    "static_html", "crawl", "focused", "incremental", "url_list", "rest",
    "graphql", "form", "sitemap", "feed", "browser", "file", "media",
    "websocket", "sse", "long_poll", "redis", "scrapy",
    "site_wordpress", "site_drupal", "site_mediawiki", "site_discourse",
}

# AI defaults merged from former standalone AI_CONFIG_DEFAULTS (2.2.0)
DEFAULTS: dict[str, Any] = {
    "ai": {
    "mode": "disabled", "default_provider": "", "providers": {},
    "routing": {}, "fallback": ["deterministic"],
    "privacy": {
        "allow_page_text": False,
        "allow_pdf_content": False,
        "allow_screenshots": False,
        "allow_cookies": False,
    },
    "budget": {
        "max_cost": 0.0,
        "max_tokens_per_request": 4096,
        "log_calls": True,
        "maximum_requests": 0, "maximum_input_characters": 0,
    },
},
    "config_version": CURRENT_CONFIG_VERSION,
    "project": {"name": "omnicrawl_project", "workspace": "work/default"},
    "source": {"kind": "static_html", "seeds": []},
    "crawl": {
        "strategy": "bfs", "max_pages": 100, "max_depth": 3,
        "same_host": True, "allow_domains": [], "deny_patterns": [],
        "allow_patterns": [], "focus_keywords": [], "concurrency": 4,
    },
    "http": {
        "user_agent": user_agent("+contact: change-me@example.com"),
        "timeout_seconds": 25, "retries": 3, "delay_seconds": 1.0,
        "retry_base_seconds": 1.0, "retry_max_seconds": 30.0,
        "retry_jitter": 0.25, "max_redirects": 10,
        "auto_browser_fallback": True,
        "respect_robots": True, "robots_fail_closed": True,
        "robots_cache_ttl_seconds": 3600, "robots_max_bytes": 2_000_000,
        "verify_tls": True, "max_response_bytes": 50_000_000,
        "allow_private_network": False, "resolve_dns": True,
        "dns_fail_closed": True, "dns_cache_ttl_seconds": 60,
        "headers": {}, "proxy": "",
    },
    "egress": {
        "enabled": True,
        "allowed_schemes": ["http", "https", "ws", "wss"],
        "allowed_ports": [],
        "allowed_domains": [],
        "credential_domains": [],
        "credential_purposes": [
            "fetch", "login", "redirect", "robots", "browser", "stream", "ai", "storage", "plugin"
        ],
        "maximum_requests": 0,
        "maximum_bytes": 0,
        "maximum_concurrency": 0,
        "maximum_runtime_seconds": 0,
        "maximum_cost": 0,
        "circuit_failure_threshold": 5,
        "circuit_recovery_seconds": 30,
        "audit": True,
        "allow_unintercepted_selenium": False,
        "experimental_selenium_bidi_guard": False,
    },
    "browser": {
        "engine": "playwright", "headless": True, "pool_size": 2, "actions": [],
        "capture_api_responses": True, "max_api_response_bytes": 1_000_000,
        "max_api_capture_bytes": 10_000_000,
        "auto_generate_api_templates": True,
    },
    "session": {"persist_cookies": False, "name": "default"},
    "auth": {"provider": "", "options": {}},
    "extract": {
        "mode": "auto", "parser": "", "extractor": "", "item_selector": "", "fields": {},
        "quality_threshold": 0.8, "review_low_confidence": True,
    },
    "data_quality": {
        "entity_fields": [],
        "entity_resolution": {"aliases": {}, "csv": ""},
        "near_duplicate_fields": ["title", "text"],
        "near_duplicate_hamming": 3,
        "near_duplicate_max_records": 5000,
    },
    "transformers": [],
    "download": {
        "enabled": False,
        "extensions": [".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".zip"],
        "media": False,
    },
    "selection": {
        "topic": {
            "enabled": False,
            "include_any": [], "include_all": [], "exclude": [],
            "match_on": ["url", "anchor", "title", "heading", "text"],
            "keep_uncertain": True, "filter_records": True,
        }
    },
    "updates": {
        "enabled": False, "revisit_completed": False,
        "detect_same_url_changes": True, "keep_versions": True,
        "confirm_missing_runs": 2,
    },
    "incremental": {"skip_unchanged": True, "archive_raw": True},
    "regression": {"enabled": True, "max_fixtures": 50},
    "processors": {
        "pdf": {
            "enabled": False,
            "config": DEFAULT_PDF_TEMPLATE,
            "project_config": "",
            "skip_ocr": False,
        }
    },
    "outputs": {
        "jsonl": True, "csv": True, "xlsx": True, "parquet": False, "duckdb": False,
        "exporter": "default", "plugin_exporters": [],
    },
    "storage": {
        "objects": {"backend": "local", "local_directory": "."},
        "records": {"backends": [], "fail_open": True, "max_errors": 200},
        "retention": {"raw_days": None, "artifacts_days": None, "diagnostics_days": None},
    },
    "plugins": {
        "paths": [], "allow_external_paths": False, "fail_open": False,
        "hook_fail_open": False, "approved_permissions": [],
    },
    "resources": {
        "profile": "balanced",
        "minimum_free_disk_bytes": 536_870_912,
        "maximum_runtime_seconds": 0,
        "maximum_workspace_bytes": 0,
        "check_interval_seconds": 5,
    },
    "diagnostics": {
        "retention_days": 30,
        "max_files": 500,
        "max_bytes": 500 * 1024 * 1024,
    },
}


@dataclass(slots=True)
class AppConfig:
    path: Path
    root: Path
    raw: dict[str, Any]
    workspace: Path
    migration_notes: tuple[str, ...] = ()

    @property
    def project_name(self) -> str:
        return str(self.raw["project"]["name"])

    @property
    def source_kind(self) -> str:
        return str(self.raw["source"]["kind"]).lower()

    def section(self, name: str) -> dict[str, Any]:
        value = self.raw.get(name, {})
        return value if isinstance(value, dict) else {}

    def resolve(self, value: str | Path) -> Path:
        path = Path(value).expanduser()
        return path.resolve() if path.is_absolute() else (self.root / path).resolve()


def resolve_pdf_template(config: AppConfig, value: str | Path = DEFAULT_PDF_TEMPLATE) -> Path:
    raw = str(value).strip() or DEFAULT_PDF_TEMPLATE
    normalized = raw.replace("\\", "/")
    if normalized.startswith("builtin:pdf/"):
        return resolve_builtin_pdf_reference(raw)
    configured = config.resolve(raw)
    legacy_default = normalized == LEGACY_DEFAULT_PDF_TEMPLATE or normalized.endswith("/" + LEGACY_DEFAULT_PDF_TEMPLATE)
    if configured.is_file() or (normalized != DEFAULT_PDF_TEMPLATE and not legacy_default):
        return configured
    return resolve_builtin_pdf_reference(DEFAULT_PDF_TEMPLATE)


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).expanduser().resolve()
    if not config_path.is_file():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("配置文件顶层必须是YAML对象")
    raw, migration_notes = migrate_config(raw)
    merged = resolve_secret_refs(deep_merge(DEFAULTS, expand_env(raw)))
    root = config_path.parent
    project_root_found = False
    for candidate in [config_path.parent, *config_path.parents]:
        if (candidate / "pyproject.toml").is_file():
            root = candidate
            project_root_found = True
            break
    if not project_root_found:
        for candidate in config_path.parents:
            if candidate.name == "configs":
                root = candidate.parent
                break
    workspace_value = merged["project"].get("workspace", "work/default")
    workspace = Path(workspace_value).expanduser()
    if not workspace.is_absolute():
        workspace = (root / workspace).resolve()
    config = AppConfig(config_path, root.resolve(), merged, workspace, migration_notes)
    errors, _warnings = validate_config(config)
    if errors:
        raise ValueError("；".join(errors))
    return config


def validate_config(config: AppConfig) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if config.source_kind not in SOURCE_KINDS:
        if config.section("plugins").get("paths"):
            warnings.append(f"source.kind={config.source_kind}将由本地插件提供，运行时再验证")
        else:
            errors.append(f"source.kind不支持: {config.source_kind}")
    seeds = config.section("source").get("seeds", [])
    if config.source_kind not in {"redis", "scrapy"} and not isinstance(seeds, list):
        errors.append("source.seeds必须是URL数组")
    if config.source_kind not in {"redis", "scrapy"} and not seeds:
        errors.append("source.seeds至少需要一个入口")
    crawl = config.section("crawl")
    if str(crawl.get("strategy", "bfs")) not in {"bfs", "dfs", "priority", "random"}:
        errors.append("crawl.strategy只能是bfs、dfs、priority或random")
    for key, minimum, maximum in (("max_pages", 1, 1_000_000), ("concurrency", 1, 128), ("max_depth", 0, 100)):
        try:
            value = int(crawl.get(key, DEFAULTS["crawl"][key]))
            if not minimum <= value <= maximum:
                errors.append(f"crawl.{key}必须在{minimum}到{maximum}之间")
        except (TypeError, ValueError):
            errors.append(f"crawl.{key}必须是整数")
    http = config.section("http")
    try:
        if float(http.get("delay_seconds", 1)) < 0:
            errors.append("http.delay_seconds不能为负数")
        if int(http.get("max_response_bytes", 0)) < 1024:
            errors.append("http.max_response_bytes不能小于1024")
        if not 0 <= int(http.get("max_redirects", 10)) <= 50:
            errors.append("http.max_redirects必须在0到50之间")
        if float(http.get("retry_max_seconds", 30)) < 0:
            errors.append("http.retry_max_seconds不能为负数")
        if int(http.get("robots_max_bytes", 2_000_000)) < 1024:
            errors.append("http.robots_max_bytes不能小于1024")
    except (TypeError, ValueError):
        errors.append("HTTP数值配置无效")
    if not http.get("respect_robots", True):
        warnings.append("robots.txt检查已关闭；请确认你拥有明确授权")
    if str(http.get("engine", "urllib")).lower() not in {"urllib", "httpx_async"}:
        errors.append("http.engine只能是urllib或httpx_async")
    if "change-me@" in str(http.get("user_agent", "")):
        warnings.append("请把User-Agent中的联系邮箱改为真实维护者邮箱")
    egress = config.section("egress")
    if not isinstance(egress.get("allowed_schemes", []), list):
        errors.append("egress.allowed_schemes必须是数组")
    elif not set(map(str, egress.get("allowed_schemes", []))).issubset({"http", "https", "ws", "wss"}):
        errors.append("egress.allowed_schemes包含不支持的协议")
    for key in ("allowed_ports", "allowed_domains", "credential_domains", "credential_purposes"):
        if not isinstance(egress.get(key, []), list):
            errors.append(f"egress.{key}必须是数组")
    try:
        for key in ("maximum_requests", "maximum_bytes", "maximum_concurrency"):
            if int(egress.get(key, 0)) < 0:
                errors.append(f"egress.{key}不能为负数")
        for key in ("maximum_runtime_seconds", "maximum_cost"):
            if float(egress.get(key, 0)) < 0:
                errors.append(f"egress.{key}不能为负数")
    except (TypeError, ValueError):
        errors.append("egress预算必须是数值")
    try:
        if int(egress.get("circuit_failure_threshold", 5)) < 1:
            errors.append("egress.circuit_failure_threshold必须至少为1")
        if float(egress.get("circuit_recovery_seconds", 30)) < 0:
            errors.append("egress.circuit_recovery_seconds不能为负数")
    except (TypeError, ValueError):
        errors.append("egress熔断配置必须是数值")
    if not isinstance(egress.get("allow_unintercepted_selenium", False), bool):
        errors.append("egress.allow_unintercepted_selenium必须是true或false")
    if not isinstance(egress.get("experimental_selenium_bidi_guard", False), bool):
        errors.append("egress.experimental_selenium_bidi_guard必须是true或false")
    if config.source_kind == "browser" and not config.section("browser").get("engine"):
        errors.append("browser.engine不能为空")
    pagination = config.section("source").get("pagination", {})
    if pagination and not isinstance(pagination, dict):
        errors.append("source.pagination必须是YAML对象")
    elif isinstance(pagination, dict) and pagination.get("type") == "page":
        try:
            start = int(pagination.get("start", 1))
            end = int(pagination.get("end", start))
            if start < 0 or end < start:
                errors.append("source.pagination页码范围无效")
        except (TypeError, ValueError):
            errors.append("source.pagination.start/end必须是整数")
        if not str(pagination.get("parameter", "page")).strip():
            errors.append("source.pagination.parameter不能为空")
    fields = config.section("extract").get("fields", {})
    if fields and not isinstance(fields, dict):
        errors.append("extract.fields必须是字段名到规则的映射")
    if not isinstance(config.section("auth").get("options", {}), dict):
        errors.append("auth.options must be a mapping")
    if not isinstance(config.raw.get("transformers", []), list):
        errors.append("transformers must be a list")
    selection = config.section("selection")
    topic = selection.get("topic", {})
    if topic and not isinstance(topic, dict):
        errors.append("selection.topic必须是YAML对象")
    elif isinstance(topic, dict):
        for key in ("include_any", "include_all", "exclude", "match_on"):
            if not isinstance(topic.get(key, []), list):
                errors.append(f"selection.topic.{key}必须是数组")
    updates = config.section("updates")
    if updates and not isinstance(updates, dict):
        errors.append("updates必须是YAML对象")
    elif isinstance(updates, dict):
        try:
            if int(updates.get("confirm_missing_runs", 2)) < 1:
                errors.append("updates.confirm_missing_runs必须至少为1")
        except (TypeError, ValueError):
            errors.append("updates.confirm_missing_runs必须是整数")
    ai = config.section("ai")
    ai_mode = str(ai.get("mode", "disabled")).casefold()
    if ai_mode not in {"disabled", "local", "cloud", "custom"}:
        errors.append("ai.mode只能是disabled、local、cloud或custom")
    if not isinstance(ai.get("providers", {}), dict) or not isinstance(ai.get("routing", {}), dict):
        errors.append("ai.providers和ai.routing必须是YAML对象")
    if ai_mode != "disabled" and not ai.get("default_provider") and not ai.get("routing"):
        warnings.append("AI已启用，但尚未为任何能力选择provider；系统将使用确定性回退")
    outputs = config.section("outputs")
    if not isinstance(outputs.get("plugin_exporters", []), list):
        errors.append("outputs.plugin_exporters must be a list")
    resources = config.section("resources")
    if str(resources.get("profile", "balanced")).casefold() not in {
        "economy", "balanced", "performance"
    }:
        errors.append("resources.profile must be economy, balanced or performance")
    try:
        for key in ("minimum_free_disk_bytes", "maximum_workspace_bytes"):
            if int(resources.get(key, 0)) < 0:
                errors.append(f"resources.{key} cannot be negative")
        for key in ("maximum_runtime_seconds", "check_interval_seconds"):
            if float(resources.get(key, 0)) < 0:
                errors.append(f"resources.{key} cannot be negative")
    except (TypeError, ValueError):
        errors.append("resources limits must be numeric")
    diagnostics_raw = config.raw.get("diagnostics", {})
    if not isinstance(diagnostics_raw, dict):
        errors.append("diagnostics必须是YAML对象")
        diagnostics: dict[str, Any] = {}
    else:
        diagnostics = diagnostics_raw
    allowed_diagnostic_keys = {"retention_days", "max_files", "max_bytes"}
    unknown_diagnostic_keys = sorted(set(diagnostics) - allowed_diagnostic_keys)
    if unknown_diagnostic_keys:
        errors.append(f"diagnostics包含未知字段: {', '.join(unknown_diagnostic_keys)}")
    for key, minimum, maximum in (
        ("retention_days", 1, 3650),
        ("max_files", 1, 100_000),
        ("max_bytes", 1024, 100 * 1024 * 1024 * 1024),
    ):
        try:
            value = int(diagnostics.get(key, DEFAULTS["diagnostics"][key]))
            if not minimum <= value <= maximum:
                errors.append(f"diagnostics.{key}必须在{minimum}到{maximum}之间")
        except (TypeError, ValueError):
            errors.append(f"diagnostics.{key}必须是整数")
    record_storage = config.section("storage").get("records", {})
    if not isinstance(record_storage, dict):
        errors.append("storage.records must be a mapping")
    elif not isinstance(record_storage.get("backends", []), list):
        errors.append("storage.records.backends must be a list")
    else:
        try:
            if int(record_storage.get("max_errors", 200)) < 0:
                errors.append("storage.records.max_errors不能为负数")
        except (TypeError, ValueError):
            errors.append("storage.records.max_errors必须是整数")
    pdf_raw = config.section("processors").get("pdf", {})
    if not isinstance(pdf_raw, dict):
        errors.append("processors.pdf必须是YAML对象")
        pdf: dict[str, Any] = {}
    else:
        pdf = pdf_raw
    if pdf.get("enabled", False):
        project_value = str(pdf.get("project_config", "")).strip()
        template_value = str(pdf.get("config", "")).strip()
        template_path = resolve_pdf_template(config, template_value or DEFAULT_PDF_TEMPLATE)
        if project_value:
            if not config.resolve(project_value).is_file():
                errors.append(f"processors.pdf.project_config不存在: {config.resolve(project_value)}")
        elif not template_path.is_file():
            errors.append(
                f"processors.pdf.config模板不存在: "
                f"{template_path}"
            )
        backend = str(pdf.get("ocr_backend", "none")).lower()
        if backend not in {"none", "paddle", "tesseract"}:
            errors.append("processors.pdf.ocr_backend只能是none、paddle或tesseract")
        if not isinstance(pdf.get("skip_ocr", False), bool):
            errors.append("processors.pdf.skip_ocr必须是true或false")
        raw_extensions = config.section("download").get("extensions", [])
        extensions = (
            {str(value).lower() for value in raw_extensions}
            if isinstance(raw_extensions, list)
            else set()
        )
        if not config.section("download").get("enabled", False) or ".pdf" not in extensions:
            warnings.append("PDF处理器已启用，但download未启用或extensions中没有.pdf")
    return errors, warnings
