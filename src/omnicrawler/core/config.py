from __future__ import annotations

import difflib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..pdfx.templates import DEFAULT_PDF_TEMPLATE, LEGACY_DEFAULT_PDF_TEMPLATE, resolve_builtin_pdf_reference
from .credentials import resolve_secret_refs
from .errors import ConfigParseError
from .migrations import CURRENT_CONFIG_VERSION, migrate_config
from .utils import deep_merge, expand_env_checked, user_agent

LOGGER = logging.getLogger("omnicrawler")

SOURCE_KINDS = {
    "static_html", "crawl", "focused", "incremental", "url_list", "rest",
    "graphql", "form", "sitemap", "feed", "browser", "file", "media",
    "websocket", "sse", "long_poll", "redis", "scrapy",
    "site_wordpress", "site_drupal", "site_mediawiki", "site_discourse",
}

# AI defaults merged from former standalone AI_CONFIG_DEFAULTS
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
    "project": {"name": "omnicrawler_project", "workspace": "work/default"},
    "source": {"kind": "static_html", "seeds": []},
    "crawl": {
        "strategy": "bfs", "max_pages": 100, "max_depth": 3,
        "same_host": True, "allow_domains": [], "deny_patterns": [],
        "allow_patterns": [], "focus_keywords": [], "concurrency": 4,
    },
    "http": {
        "engine": "urllib",
        "user_agent": user_agent("+contact: change-me@example.com"),
        "timeout_seconds": 25, "retries": 3, "delay_seconds": 1.0,
        "retry_base_seconds": 1.0, "retry_max_seconds": 30.0,
        "retry_jitter": 0.25,
        "retry_on_status": [408, 425, 429, 500, 502, 503, 504],
        "max_redirects": 10,
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
        # S2.5.12：Selenium BiDi 逐请求拦截默认开启（安全默认），
        # 引擎不支持或拦截不可用时给出清晰指引而非静默直连
        "experimental_selenium_bidi_guard": True,
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
    # AutoDataCleaner 值清洗：L1 幂等 + L2 规则默认开，L3（LLM）槽位默认关
    "quality": {
        "normalize": {
            "enabled": True,
            "l1_enabled": True,
            "l2_enabled": True,
            "l3_enabled": False,
            "money_unit": "元",
            "date_format": "iso",
            "strip_tracking": True,
            "types": {},
        }
    },
    "incremental": {"skip_unchanged": True, "archive_raw": True},
    "regression": {"enabled": True, "max_fixtures": 50},
    "processors": {
        "pdf": {
            "enabled": False,
            "config": DEFAULT_PDF_TEMPLATE,
            "project_config": "",
            "skip_ocr": False,
            "ocr_backend": "none",
        }
    },
    "outputs": {
        "jsonl": True, "csv": True, "xlsx": True, "parquet": False, "duckdb": False,
        "exporter": "default", "plugin_exporters": [],
    },
    "storage": {
        "objects": {"backend": "local", "local_directory": "."},
        "records": {"backends": [], "fail_open": False, "max_errors": 200},
        "retention": {"raw_days": None, "artifacts_days": None, "diagnostics_days": None},
    },
    "plugins": {
        "paths": ["plugins/", "plugins_installed/"], "allow_external_paths": False, "fail_open": False,
        "hook_fail_open": False, "approved_permissions": [],
        "trust_public_key": "",
        # 签名策略：strict（默认）= 未签名拒载、本地创作者签名经信任询问确认；
        # developer = 未验签/未信任作者时警告放行（仅测试与本地开发）。
        # 市场目录（plugins_installed/）的插件始终要求维护者签名（信任根）。
        "signature_policy": "strict",
        # AST 静态检查豁免列表（加载前拦截 subprocess/ctypes/eval/os.system 等危险模式）。
        # 与插件文件内 "# omnicrawler: allow-ast <名称>" 注释等效；豁免必须显式、可审计。
        "ast_allowed_patterns": [],
        # 插件市场 catalog 基址（单一可迁移配置）。所有 catalog 内部路径均为相对此基址
        # 的相对路径；迁移到独立仓库或自托管 HTTP 服务时，只需改这一项。
        # 默认连在线独立市场仓（GitHub raw 基址）；插件/模板上架即时可见、无需重新发版。
        # 联网失败自动回退 bundled_catalog_dir 内置离线快照。迁移/自托管改这一项即可。
        "catalog_url": "https://raw.githubusercontent.com/Starlife-creator/OmniCrawler-market/main",
        # 离线/便携构建内置的 catalog 快照目录（相对或绝对路径，相对应用根解析）。
        # 默认 "market"：仓库根 market/ 为内置快照（出包时打入 catalog.json + 信任根公钥 + 首发模板），
        # 用户开箱即可离线浏览/安装市场；找不到时回退 local_fallback（../OmniCrawler-market）或 catalog_url。
        "bundled_catalog_dir": "market",
        # ---- Phase 2a B5：子进程运行时配置键 ----
        # 三态：auto（按路由裁决）/ force_subprocess（总闸）/ legacy_in_process（逃生开关）。
        "runtime_backend": "auto",
        # 豁免表：[{plugin_id, version_range, reason, expires（必填，ISO8601）}]；
        # 显式强制进程内（绕过批准矩阵）；解析损坏 → fail-closed 视为空表。
        "in_process_allowlist": [],
        # 逃生开关（另可用环境变量 OMNICRAWL_ALLOW_UNSANDBOXED_PLUGIN=1 双通道）；
        # 每次启用写审计。
        "sandbox_escape": False,
        # 额外信任根公钥列表（企业私有市场/自建根场景，G1 联动 catalog 签名）。
        "trust_roots": [],
        # 启动时（有网）拉 catalog 检查已装插件版本是否被吊销（G2）。
        "revocation_check": True,
        # 会话调用超时；冻结冷启动握手单独放宽 60s（onefile 自解压，C1）。
        "subprocess_timeout_seconds": 30,
        # Job Object / rlimit 内存上限（MB）。
        "subprocess_memory_mb": 512,
        # 全局并发会话上限（N×512MB 资源总和控制；作用于会话）。
        "subprocess_max_concurrent": 4,
        # 会话崩溃后是否自动重 spawn（默认否，保持确定性）。
        "session_restart_on_error": False,
        # 插件每日请求/字节配额（E_QUOTA 来源；与 maximum_requests 构成双层量约束）。
        "network_daily_quota": {},
        # 数据外传关联检测策略：prompt（个人默认，共现提示）/ block（企业档，共现阻断）。
        "egress_policy": "prompt",
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
    warnings: tuple[str, ...] = ()

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

    @property
    def plugin_trust_public_key(self) -> str:
        """ed25519 信任根公钥（PEM 或公钥文件路径），用于插件 fail-closed 验签。

        未显式配置时**默认接线内置信任根** ``configs/plugin_trust.pub.pem``
        （审查报告 B5：内置公钥默认不接线，开箱即用市场插件必然拒载）。
        内置文件不存在（如 pip 安装形态）时回退空串——加载门显式告警而非
        静默接受（S51）。
        """
        plugins = self.section("plugins")
        value = plugins.get("trust_public_key", "") if isinstance(plugins, dict) else ""
        if value:
            return str(value)
        bundled = self.root / "configs" / "plugin_trust.pub.pem"
        return str(bundled) if bundled.is_file() else ""

    @property
    def plugin_catalog_url(self) -> str:
        """插件市场 catalog 基址（单一可迁移配置）。

        所有 catalog 内部文件路径均为相对于此基址的相对路径。迁移到独立仓库或
        自托管 HTTP 服务时，只需修改此值（见 market/ 离线快照或
        OmniCrawler-market/README.md），catalog 内部无需任何改动。空串表示不配置
        远程市场，GUI 市场面板回退 bundled_catalog_dir。
        """

        plugins = self.section("plugins")
        value = plugins.get("catalog_url", "") if isinstance(plugins, dict) else ""
        return str(value) if value else ""

    @property
    def plugin_bundled_catalog_dir(self) -> str:
        """离线/便携构建内置的 catalog 快照目录（相对或绝对路径）。

        留空表示没有内置快照（市场仅在线可用）。便携包在构建期把 market/ 离线快照
        打包进应用并填此路径，可使市场离线也可用。路径相对于应用根目录解析。
        """

        plugins = self.section("plugins")
        value = plugins.get("bundled_catalog_dir", "") if isinstance(plugins, dict) else ""
        return str(value) if value else ""


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
    # S2.1.2 ②：YAML 语法错误包装为含行列号的友好提示，原始异常保留到日志
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        position = f"，第 {mark.line + 1} 行第 {mark.column + 1} 列" if mark is not None else ""
        problem = getattr(exc, "problem", None) or str(exc)
        LOGGER.error("YAML 语法错误: %s", exc, exc_info=True)
        raise ConfigParseError(f"配置文件 {config_path} 存在语法错误: {problem}{position}") from exc
    if not isinstance(raw, dict):
        raise ValueError("配置文件顶层必须是YAML对象")
    raw, migration_notes = migrate_config(raw)
    expanded, missing_vars = expand_env_checked(raw)
    merged = resolve_secret_refs(deep_merge(DEFAULTS, expanded))
    _apply_retry_alias(merged, expanded)
    root = config_path.parent
    # S4.2 ⑥：显式 project.root 优先（固定策略，不再跨环境漂移）；
    # 否则 pyproject.toml → configs/ 目录的确定性探测
    declared_root = merged["project"].get("root") if isinstance(merged["project"], dict) else None
    if declared_root:
        root = Path(str(declared_root)).expanduser()
        if not root.is_absolute():
            root = (config_path.parent / root).resolve()
    else:
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
    errors, warnings = validate_config(config)
    if missing_vars:
        warnings.append(
            f"环境变量未定义已替换为空串，共 {len(missing_vars)} 个: " + "、".join(sorted(missing_vars))
        )
    if errors:
        # S2.1.2 ①：错误多行、带编号，不再 ", ".join 挤一行
        lines = "\n".join(f"  [{index}] {message}" for index, message in enumerate(errors, 1))
        raise ConfigParseError(f"配置校验失败，共 {len(errors)} 项错误：\n{lines}")
    config.warnings = tuple(warnings)
    return config


def _closest_hint(known: list[str], key: str) -> str:
    """给拼写错误配置键一个最接近的合法键候选提示（difflib 相似度 >= 0.6）。"""
    candidates = difflib.get_close_matches(key, known, n=1, cutoff=0.6)
    return f"，是否想写 '{candidates[0]}'？" if candidates else ""


def _apply_retry_alias(merged: dict[str, Any], expanded: dict[str, Any]) -> None:
    """双轨合并（S2.1.4）：旧轨 `http.retry_max` 写进新轨 `http.retries`。

    merged 恒含 DEFAULTS 的 retries，无法区分用户是否显式配置；因此只在用户
    YAML（expanded）中出现 retry_max 时覆盖，retries 仍优先（两者都写时 retries 胜）。
    retries 语义为"总尝试次数"：0 表示不重试。非法 retry_max（负数/非整数）忽略，
    由 validate_config 报错。
    """
    raw_http = expanded.get("http")
    if not isinstance(raw_http, dict):
        return
    if "retries" in raw_http:
        return  # 新轨显式配置优先，retry_max 仅在未写 retries 时兜底
    retry_max = raw_http.get("retry_max")
    if retry_max is None:
        return
    try:
        count = int(retry_max)
    except (TypeError, ValueError):
        return
    if count < 0:
        return
    merged["http"]["retries"] = count


def _check_contained(
    key: str, resolved: Path, root: Path, flag: Callable[[str], None],
) -> None:
    """B05-010：校验绝对路径配置值解析后位于 root 内（真实前缀包含）。"""
    try:
        resolved.relative_to(root)
    except ValueError:
        flag(f"{key} 解析后位于项目根之外: {resolved}（项目根: {root}）")


def require_config_path(path: str | Path, *, require_inside_cwd: bool = False) -> Path:
    """B09-003：消费外部可控 config_path 前校验。

    queue/schedule 的 config_path 来自入队 TaskSpec / 调度库，共享模式下可被
    外部方控制。消费前必须：存在且可读；本地降级模式额外要求位于 CWD 内，
    避免加载越界/攻击者构造的配置。
    """
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"任务配置不存在或不可读: {resolved}")
    if require_inside_cwd:
        cwd = Path.cwd().resolve()
        try:
            resolved.relative_to(cwd)
        except ValueError:
            raise ValueError(f"本地队列模式下 config_path 必须位于当前目录内: {resolved}")
    return resolved


def validate_config(config: AppConfig, *, strict: bool = False) -> tuple[list[str], list[str]]:
    """校验配置。

    strict=False（默认）时，未知段/未知字段以 warning 提示（DEFAULTS 合并后
    白名单外的键只能是用户写错的键，不会误伤内置默认值）；strict=True 时升级为 error。

    Returns:
        (errors, warnings) 元组。
    """
    errors: list[str] = []
    warnings: list[str] = []
    flag = errors.append if strict else warnings.append

    # S2.1.1 ②：顶层段白名单（DEFAULTS 键 + GUI/模板允许的扩展段）
    known_sections = set(DEFAULTS) | {"api_discovery", "task", "template"}
    for key in sorted(set(config.raw) - known_sections):
        hint = _closest_hint(sorted(known_sections), key)
        flag(
            f"配置包含未知顶层段 '{key}'，允许的段: {', '.join(sorted(known_sections))}{hint}"
        )

    # 固定结构段的白名单子键（未知键只能是用户拼写错误）
    # FINAL-D2：与实际消费点对齐——以下键被运行时读取但不在 DEFAULTS
    # （crawl.max_requests/_run.py、crawl.wait_timeout_seconds/_run.py、
    # extract.enrich|scene/_extract.py、extract.{parser,processor,extractor}_options/
    # _builders.py、outputs.exporter_options/_exports.py、source.categorizer/
    # categorizer.py），此前会被误报"未知字段"，稀释拼写检查告警价值。
    section_whitelist = {
        "browser": set(DEFAULTS["browser"]),
        "crawl": set(DEFAULTS["crawl"]) | {"max_requests", "wait_timeout_seconds"},
        "download": set(DEFAULTS["download"]) | {"output_dir"},
        "egress": set(DEFAULTS["egress"]),
        "extract": set(DEFAULTS["extract"]) | {
            "item_path", "enrich", "scene",
            "parser_options", "processor_options", "extractor_options",
        },
        "http": set(DEFAULTS["http"]) | {"retry_max"},
        "incremental": set(DEFAULTS["incremental"]) | {"since_date"},
        "outputs": set(DEFAULTS["outputs"]) | {"exporter_options"},
        "plugins": set(DEFAULTS["plugins"]),
        "quality": set(DEFAULTS["quality"]),
        "resources": set(DEFAULTS["resources"]),
        "session": set(DEFAULTS["session"]),
        "updates": set(DEFAULTS["updates"]),
        # S3.3.2：source 是核心段——漏检会让 seedz 等拼写错误静默通过
        "source": set(DEFAULTS["source"]) | {
            "method", "headers", "payload", "content_type", "pagination",
            "login", "max_messages", "duration_seconds", "subscribe",
            "fields", "params", "variables", "arguments", "query",
            "query_file", "spider_file", "max_pages",
            # B-2 闸门：逐 URL 模板强制覆盖（GUI 写入，Worker/Runner 消费）
            "seed_template_overrides",
            # FINAL-D2：站点分类器配置（categorizer.py 消费 enable_sniffing 等）
            "categorizer",
        },
    }
    for section, allowed in section_whitelist.items():
        raw_section = config.raw.get(section)
        if not isinstance(raw_section, dict):
            continue
        for key in sorted(set(raw_section) - allowed):
            hint = _closest_hint(sorted(allowed), key)
            flag(
                f"配置段 '{section}' 包含未知字段 '{key}'，允许: {', '.join(sorted(allowed))}{hint}"
            )

    # 嵌套固定结构（storage.* / processors.pdf）
    nested_whitelist = {
        "storage": {
            "objects": set(DEFAULTS["storage"]["objects"]),
            "records": set(DEFAULTS["storage"]["records"]),
            "retention": set(DEFAULTS["storage"]["retention"]),
        },
        "processors": {"pdf": set(DEFAULTS["processors"]["pdf"])},
        "quality": {"normalize": set(DEFAULTS["quality"]["normalize"])},
    }
    for section, nested in nested_whitelist.items():
        raw_section = config.raw.get(section)
        if not isinstance(raw_section, dict):
            continue
        for child, allowed in nested.items():
            raw_child = raw_section.get(child)
            if not isinstance(raw_child, dict):
                continue
            for key in sorted(set(raw_child) - allowed):
                hint = _closest_hint(sorted(allowed), key)
                flag(
                    f"配置段 '{section}.{child}' 包含未知字段 '{key}'，"
                    f"允许: {', '.join(sorted(allowed))}{hint}"
                )

    # B05-013：project.name / source.kind 类型守卫——非字符串直接报错，
    # 避免 str() 强转掩盖类型错误（如数字/字典被转成 "..." 语义漂移）。
    raw_project = config.raw.get("project", {})
    if not isinstance(raw_project.get("name", ""), str):
        errors.append("project.name必须是字符串")
    raw_source_kind = config.raw.get("source", {}).get("kind", "")
    if not isinstance(raw_source_kind, str):
        errors.append("source.kind必须是字符串")

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
        if http.get("retry_max") is not None:
            if isinstance(http.get("retry_max"), bool):
                errors.append("http.retry_max必须是大于等于0的整数（0 表示不重试）")
            else:
                try:
                    retry_max_value = int(str(http.get("retry_max")))
                except (TypeError, ValueError):
                    errors.append("http.retry_max必须是大于等于0的整数（0 表示不重试）")
                else:
                    if retry_max_value < 0:
                        errors.append("http.retry_max必须是大于等于0的整数（0 表示不重试）")
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
        errors.append("auth.options必须是YAML对象")
    if not isinstance(config.raw.get("transformers", []), list):
        errors.append("transformers必须是数组")
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
    quality = config.section("quality")
    if quality and not isinstance(quality, dict):
        errors.append("quality必须是YAML对象")
    elif isinstance(quality, dict):
        norm = quality.get("normalize", {})
        if norm and not isinstance(norm, dict):
            errors.append("quality.normalize必须是YAML对象")
        elif isinstance(norm, dict):
            from ..quality.normalizers import _TYPE_KINDS

            for qflag in ("enabled", "l1_enabled", "l2_enabled", "l3_enabled", "strip_tracking"):
                if qflag in norm and not isinstance(norm[qflag], bool):
                    errors.append(f"quality.normalize.{qflag}必须是布尔")
            types = norm.get("types", {})
            if types and not isinstance(types, dict):
                errors.append("quality.normalize.types必须是YAML对象")
            elif isinstance(types, dict):
                for key, value in types.items():
                    if str(value) not in _TYPE_KINDS:
                        errors.append(
                            f"quality.normalize.types.{key}必须是{list(_TYPE_KINDS)}之一"
                        )
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
        errors.append("outputs.plugin_exporters必须是数组")
    # E15：至少启用一种导出格式，否则 run 结束只产出辅助文件——以 warning 提示，
    # 不阻止加载（分析类任务可能有意只留 state.sqlite3）
    enabled_formats = [key for key in ("jsonl", "csv", "xlsx") if bool(outputs.get(key, False))]
    if not enabled_formats and not outputs.get("plugin_exporters"):
        warnings.append("outputs未启用任何导出格式（jsonl/csv/xlsx）或 plugin_exporters，运行只会产出辅助/状态文件")
    resources = config.section("resources")
    if str(resources.get("profile", "balanced")).casefold() not in {
        "economy", "balanced", "performance"
    }:
        errors.append("resources.profile只能是economy、balanced或performance")
    try:
        for key in ("minimum_free_disk_bytes", "maximum_workspace_bytes"):
            if int(resources.get(key, 0)) < 0:
                errors.append(f"resources.{key}不能为负数")
        for key in ("maximum_runtime_seconds", "check_interval_seconds"):
            if float(resources.get(key, 0)) < 0:
                errors.append(f"resources.{key}不能为负数")
    except (TypeError, ValueError):
        errors.append("resources配置必须是数值")
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
        errors.append("storage.records必须是YAML对象")
    elif not isinstance(record_storage.get("backends", []), list):
        errors.append("storage.records.backends必须是数组")
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

    # B05-010/B05-014：绝对路径配置值 contained-in-root 校验。
    # 越界仅告警（多项目共享目录可能是合法用法），strict 时升级为 error。
    root_resolved = config.root.resolve()
    _check_contained("project.workspace", config.workspace, root_resolved, flag)
    storage_objects = config.section("storage").get("objects", {})
    storage_dir = storage_objects.get("local_directory", "") if isinstance(storage_objects, dict) else ""
    if isinstance(storage_dir, str) and storage_dir.strip():
        _check_contained(
            "storage.objects.local_directory",
            config.resolve(storage_dir), root_resolved, flag,
        )
    if pdf.get("enabled", False) and project_value:
        _check_contained(
            "processors.pdf.project_config",
            config.resolve(project_value), root_resolved, flag,
        )
    return errors, warnings
