"""应用核心配置对象（AppConfig）、加载管线与完整契约校验。"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..security import tls_scope
from .auth_policy import validate_auth_check
from .builtin_references import (
    DEFAULT_PDF_TEMPLATE,
    LEGACY_DEFAULT_PDF_TEMPLATE,
    resolve_builtin_pdf_reference,
)
from .config_defaults import (
    DEFAULTS as DEFAULTS,
)
from .config_defaults import (
    SOURCE_KINDS as SOURCE_KINDS,
)
from .config_loader import (
    _apply_retry_alias as _apply_retry_alias,
)
from .config_loader import (
    _check_contained as _check_contained,
)
from .config_loader import (
    _closest_hint as _closest_hint,
)
from .config_loader import (
    parse_yaml_mapping,
    resolve_project_root,
)
from .config_loader import (
    require_config_path as require_config_path,
)
from .credentials import resolve_secret_refs
from .errors import ConfigParseError

# 叶子模块（无 bs4 / lxml 依赖）：配置校验只需 JSONPath 语法判定。
from .json_path_syntax import (
    reject_unsupported_json_path as _reject_unsupported_json_path,
)
from .migrations import migrate_config
from .pagination import body_location_payload_error, validate_pagination
from .utils import deep_merge, expand_env_checked

LOGGER = logging.getLogger("omnicrawler")


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
    def update_trust_public_key(self) -> str:
        """应用自更新的 ed25519 信任根（PEM 文本**或**公钥文件路径）。

        未显式配置 `self_update.trusted_public_key` 时**默认接线内置信任根**
        ``configs/update_trust.pub.pem``（与 `plugin_trust_public_key` 同一写法）——
        它**刻意与市场信任根是两把钥匙**：市场根授权"沙箱内的插件"，更新根授权
        "替换应用本体"，两者权威不同级，不可互相顶替。

        内置文件不存在（如 pip 安装形态）时回退空串 ⇒ 调用方判"未配置 ⇒ 禁用"
        （fail-closed：**不存在"跳过验签"的降级路径**）。
        """
        section = self.section("self_update")
        value = section.get("trusted_public_key", "") if isinstance(section, dict) else ""
        if value:
            return str(value)
        bundled = self.root / "configs" / "update_trust.pub.pem"
        return str(bundled) if bundled.is_file() else ""

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
        """OmniCrawler 市场 catalog 基址（单一可迁移配置）。

        所有 catalog 内部文件路径均为相对于此基址的相对路径。迁移到独立仓库或
        自托管 HTTP 服务时，只需修改此值（见 market/ 离线快照或
        OmniCrawler-market/README.md），catalog 内部无需任何改动。空串表示不配置
        远程市场，GUI 市场面板回退 bundled_catalog_dir。
        """

        plugins = self.section("plugins")
        value = plugins.get("catalog_url", "") if isinstance(plugins, dict) else ""
        return str(value) if value else ""

    @property
    def plugin_catalogs(self) -> list[dict[str, Any]]:
        """多索引（#77 Phase 1）：可同时启用**多个** catalog 源。

        配置形态：``plugins.catalogs: [{url, trust?, kind?, priority?, enabled?}]``

        - ``trust``：该索引的信任根（PEM 文本 / 路径 / base64 / hex:）；留空＝用官方内置信任根；
        - ``kind``：``curated``（官方策展）/``community``（社区索引）/``topic``（主题聚合）；
        - ``priority``：同一 ``id`` 出现在多个索引时的展示优先级（小的在前，缺省 100）；
        - ``enabled``：缺省 True。

        **向后兼容**：未配置 ``catalogs`` 时，由 ``catalog_url`` 派生**唯一一条官方源**
        （行为与升级前完全一致）。 ``catalog_url`` 仍保留为官方源基址。
        """
        plugins = self.section("plugins")
        if not isinstance(plugins, dict):
            return []
        raw = plugins.get("catalogs")
        sources: list[dict[str, Any]] = []
        if isinstance(raw, list):
            for index, item in enumerate(raw):
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url") or "").strip()
                if not url:
                    continue
                kind = str(item.get("kind") or "community").strip() or "community"
                if kind not in ("curated", "community", "topic"):
                    kind = "community"
                try:
                    priority = int(item.get("priority", 100))
                except (TypeError, ValueError):
                    priority = 100
                sources.append(
                    {
                        "url": url,
                        "trust": str(item.get("trust") or "").strip(),
                        "kind": kind,
                        "priority": priority,
                        "enabled": bool(item.get("enabled", True)),
                        "index": index,
                    }
                )
        if sources:
            return sources
        # 兼容路径：单源（官方策展）
        url = self.plugin_catalog_url
        if not url:
            return []
        return [
            {
                "url": url,
                "trust": "",
                "kind": "curated",
                "priority": 0,
                "enabled": True,
                "index": 0,
            }
        ]

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
    raw = parse_yaml_mapping(config_path)
    raw, migration_notes = migrate_config(raw)
    expanded, missing_vars = expand_env_checked(raw)
    merged = resolve_secret_refs(deep_merge(DEFAULTS, expanded))
    _apply_retry_alias(merged, expanded)
    root = resolve_project_root(config_path, merged)
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
        lines_str = "\n".join(f"  [{index}] {message}" for index, message in enumerate(errors, 1))
        raise ConfigParseError(f"配置校验失败，共 {len(errors)} 项错误：\n{lines_str}")
    config.warnings = tuple(warnings)
    return config


def _validate_json_field_paths(fields: dict[str, Any]) -> list[str]:
    """校验 JSON 模式下每个字段的路径语法（2026-11-28）。

    为什么放在**配置校验**而不是留到采集时：

    `path: $..title` 此前能通过 `validate`，直到真正跑采集才抛 ValueError ——
    那时用户已经在等结果，而报错位置在抽取器里，看不出是「配置写错了」。

    判据复用 `extraction.extractors` 的同一个函数，避免「校验说行、运行说不行」。
    """
    problems: list[str] = []
    for name, rule in fields.items():
        paths: list[str] = []
        if isinstance(rule, str):
            paths = [rule]
        elif isinstance(rule, dict):
            candidates = rule.get("paths")
            if isinstance(candidates, list) and candidates:
                paths = [str(item) for item in candidates]
            elif "path" in rule:
                paths = [str(rule.get("path"))]
            elif "selector" in rule:
                paths = [str(rule.get("selector"))]
        for path in paths:
            try:
                _reject_unsupported_json_path(path)
            except ValueError as exc:
                problems.append(f"extract.fields.{name}: {exc}")
    return problems

def _validate_css_field_selectors(fields: dict[str, Any]) -> list[str]:
    """校验 HTML 模式下每个字段的 CSS 选择器语法（2026-11-28）。

    为什么放在**配置校验**而不是留到采集时：

    `selector: div[[[[broken` 此前能通过 `validate`，直到真正跑采集才抛
    `SelectorSyntaxError` —— 报错位置在解析器里，看不出是「配置写错了」。

    判据复用**引擎自己的解析器**（`cssselect`，经 `html_tools` 暴露），
    不自己写正则猜语法：那会造出「校验说行、运行说不行」的第二套判据。
    """
    from ..extraction.html_tools import selector_is_valid

    problems: list[str] = []
    for name, rule in fields.items():
        selectors: list[str] = []
        if isinstance(rule, str):
            selectors = [rule]
        elif isinstance(rule, dict):
            kind = str(rule.get("type", "css")).strip().lower()
            if kind not in {"", "css"}:
                continue
            candidates = rule.get("selectors")
            if isinstance(candidates, list) and candidates:
                selectors = [str(item) for item in candidates if isinstance(item, str)]
            elif "selector" in rule:
                selectors = [str(rule.get("selector"))]
        for selector in selectors:
            if not selector.strip():
                continue
            if not selector_is_valid(selector):
                problems.append(
                    f"extract.fields.{name}: CSS 选择器语法错误 {selector!r}"
                )
    return problems

def _validate_field_regexes(fields: dict[str, Any]) -> list[str]:
    """校验每个字段 `regex` 能否编译（2026-11-28）。

    为什么放在**配置校验**：`safe_regex_search` 编译失败时只记一条 `LOGGER.warning`
    并返回 `None` —— 那个 warning 用户在 GUI 里看不到、在 CLI 里被日志淹没。
    症状是「字段一直是空的」，而根因是「正则写错了」，两者离得很远。

    判据就是 `re.compile` 本身：不做启发式、不管 ReDoS（那是运行期防护的事，
    见 `safe_regex_search` 的 `_unsafe_regex`）。这里只回答「这串能编译吗」。
    """
    import re

    problems: list[str] = []
    for name, rule in fields.items():
        if not isinstance(rule, dict):
            continue
        raw = rule.get("regex")
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            re.compile(raw)
        except re.error as exc:
            problems.append(f"extract.fields.{name}: 正则语法错误 {raw!r}（{exc}）")
    return problems

def validate_config(config: AppConfig, *, strict: bool = False) -> tuple[list[str], list[str]]:
    """校验配置。

    strict=False（默认）时，未知段/未知字段以 warning 提示（DEFAULTS 合并后
    白名单外的键只能是用户写错的键，不会误伤内置默认值）；strict=True 时升级为 error。

    Returns:
        (errors, warnings) 元组。
    """
    errors: list[str] = []
    warnings: list[str] = []
    errors.extend(validate_auth_check(config.section("source").get("auth_check")))
    flag = errors.append if strict else warnings.append

    # S2.1.1 ②：顶层段白名单（DEFAULTS 键 + GUI/模板允许的扩展段）
    #
    # ★ 2026-11-25：补 `template_version`。它是**模板元数据版本**，由
    # `templates/capture.py` 写入，且**随包分发的 50 个模板全部带它**
    # （`gui/templates/*.yaml`、`templates/scenarios/*.yaml` 等）。
    #
    # 缺了它，用户 `init --template` 生成配置后第一件事 `validate` 就收到：
    #   配置包含未知顶层段 'template_version'
    # —— 而那是我们自己的模板写进去的，不是用户拼错。
    #
    # 这不是「放宽白名单」：`template_version` 确实是被消费的正式键
    # （见 `templates/evidence.py`、`templates/capture.py`）。
    known_sections = set(DEFAULTS) | {
        "api_discovery", "task", "template", "template_version",
    }
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
        "audit": set(DEFAULTS["audit"]),
        "tuning": set(DEFAULTS["tuning"]),
        "extract": set(DEFAULTS["extract"]) | {
            "item_path", "enrich", "scene",
            "parser_options", "processor_options", "extractor_options", "deduplicate_by",
            # 2026-11-27：内嵌 JSON 数据（Next.js / Nuxt 的 SSR 载荷）作为**兜底**数据源。
            # 静态 DOM 里没有列表、但页面内嵌了完整数据时，用它补齐而不是直接判失败。
            "embedded_json",
        },
        "http": set(DEFAULTS["http"]) | {"retry_max"},
        "incremental": set(DEFAULTS["incremental"]) | {"since_date"},
        "outputs": set(DEFAULTS["outputs"]) | {"exporter_options"},
        "plugins": set(DEFAULTS["plugins"]),
        "quality": set(DEFAULTS["quality"]),
        "resources": set(DEFAULTS["resources"]),
        "self_update": set(DEFAULTS["self_update"]),
        "session": set(DEFAULTS["session"]),
        "updates": set(DEFAULTS["updates"]),
        # S3.3.2：source 是核心段——漏检会让 seedz 等拼写错误静默通过
        "source": set(DEFAULTS["source"]) | {
            "method", "headers", "payload", "content_type", "pagination",
            "login", "max_messages", "duration_seconds", "subscribe",
            "fields", "params", "variables", "arguments", "query",
            "query_file", "spider_file", "max_pages",
            # B-2 闸门：逐 URL 模板强制覆盖（GUI 写入，Worker/Runner 消费）
            "seed_template_overrides", "follow_xpath",
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
    if type(config.section("download").get("verified_pdf_manifest", False)) is not bool:
        errors.append("download.verified_pdf_manifest必须是布尔值")

    if config.source_kind not in SOURCE_KINDS:
        if config.section("plugins").get("paths"):
            warnings.append(f"source.kind={config.source_kind}将由本地插件提供，运行时再验证")
        else:
            errors.append(f"source.kind不支持: {config.source_kind}")
    plugins = config.section("plugins")
    permission_grants = plugins.get("permission_grants", {})
    if not isinstance(permission_grants, dict):
        errors.append("plugins.permission_grants必须是按插件ID索引的映射")
    else:
        for plugin_id, grant in permission_grants.items():
            if not isinstance(plugin_id, str) or not plugin_id.strip() or not isinstance(grant, dict):
                errors.append("plugins.permission_grants条目必须是非空插件ID到授权映射")
                continue
            permissions = grant.get("permissions", [])
            if not isinstance(permissions, list) or not all(
                isinstance(permission, str) for permission in permissions
            ):
                errors.append(f"plugins.permission_grants.{plugin_id}.permissions必须是数组")
            artifact_sha256 = grant.get("artifact_sha256", "")
            if not isinstance(artifact_sha256, str) or not re.fullmatch(
                r"[0-9a-fA-F]{64}", artifact_sha256
            ):
                errors.append(
                    f"plugins.permission_grants.{plugin_id}.artifact_sha256必须是64位SHA-256"
                )
            for binding in ("version", "creator_fingerprint"):
                if binding in grant and not isinstance(grant[binding], str):
                    errors.append(
                        f"plugins.permission_grants.{plugin_id}.{binding}必须是字符串"
                    )
    enabled_market_plugins = plugins.get("enabled_market_plugins")
    if enabled_market_plugins is not None and (
        not isinstance(enabled_market_plugins, list)
        or not all(isinstance(item, str) and item.strip() for item in enabled_market_plugins)
    ):
        errors.append("plugins.enabled_market_plugins必须是插件ID字符串数组或null")
    # 无种子源：redis/scrapy 自带入口；插件提供的 source.kind（不在 SOURCE_KINDS）
    # 的入口由插件定义（如文件型插件读 source.file），不强制 seeds（见 issue #75）。
    seedless = config.source_kind in {"redis", "scrapy"} or config.source_kind not in SOURCE_KINDS
    seeds = config.section("source").get("seeds", [])
    if not seedless and not isinstance(seeds, list):
        errors.append("source.seeds必须是URL数组")
    if not seedless and not seeds:
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
    # A-P3-2：用户正则在配置校验期编译，非法模式在此拒绝（与 ScopePolicy 预编译缓存同源）。
    for pattern_key in ("deny_patterns", "allow_patterns", "allow_write_patterns"):
        patterns = crawl.get(pattern_key, [])
        if not isinstance(patterns, list):
            errors.append(f"crawl.{pattern_key}必须是正则数组")
            continue
        for pattern in patterns:
            try:
                re.compile(str(pattern))
            except re.error as exc:
                errors.append(f"crawl.{pattern_key} 正则非法 {str(pattern)!r}: {exc}")
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
        # P2-5：TLS 校验降级必须是**有作用域**的。
        # `verify_tls` 是全局开关（6 条抓取路径共用），只为访问一台自签证书的内网机器而关掉它，
        # 会连带把公网目标一起降级。因此要求显式声明允许免校验的主机，并在出网收口处强制该作用域。
        raw_scope = http.get("tls_insecure_domains", [])
        raw_entries = list(raw_scope) if isinstance(raw_scope, (list, tuple)) else []
        if not isinstance(raw_scope, (list, tuple)):
            errors.append("http.tls_insecure_domains必须是主机名数组")
        for entry in raw_entries:
            reason = tls_scope.invalid_entry_reason(entry)
            if reason:
                errors.append(f"http.tls_insecure_domains 条目非法 {entry!r}: {reason}")
        scope = tls_scope.normalized(raw_entries)
        if http.get("verify_tls", True) is False:
            if not scope:
                errors.append(
                    "http.verify_tls=false 必须同时用 http.tls_insecure_domains 显式声明"
                    "允许免校验的主机（TLS 校验只对已声明的主机关闭）；"
                    "若无需关闭请设回 http.verify_tls=true"
                )
            for host in scope:
                if not tls_scope.looks_internal(host):
                    warnings.append(
                        f"http.tls_insecure_domains 中的 {host} 看起来不是内网主机；"
                        "对它关闭 TLS 校验会使其面临中间人风险"
                    )
        elif scope:
            warnings.append(
                "http.tls_insecure_domains 已配置但 http.verify_tls=true，该名单当前不生效"
            )
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
    # A-P2-2：审计哈希链开关与严格模式类型校验（严格模式校验失败 fail-closed）。
    audit = config.section("audit")
    for _audit_key in ("chain_enabled", "strict_chain"):
        if not isinstance(audit.get(_audit_key, DEFAULTS["audit"][_audit_key]), bool):
            errors.append(f"audit.{_audit_key}必须是true或false")
    if not isinstance(audit.get("chain_path", ""), str) or not str(audit.get("chain_path", "")).strip():
        errors.append("audit.chain_path不能为空字符串")
    # A-P3-5：集中阈值 / 权重区间校验（非负数值；轮询上下限 min <= max；窗口 / 容量 >= 1）。
    tuning = config.section("tuning")
    weights = tuning.get("review_weights", DEFAULTS["tuning"]["review_weights"])
    if not isinstance(weights, dict):
        errors.append("tuning.review_weights必须是映射表")
    else:
        for _wkey, _wdefault in DEFAULTS["tuning"]["review_weights"].items():
            try:
                if float(weights.get(_wkey, _wdefault)) < 0:
                    errors.append(f"tuning.review_weights.{_wkey}不能为负数")
            except (TypeError, ValueError):
                errors.append(f"tuning.review_weights.{_wkey}必须是非负数")
    for _tkey, _minimum in (
        ("adaptive_window", 1), ("adaptive_audit_limit", 1), ("live_history_window", 1),
        ("live_audit_limit", 1), ("live_domain_capacity", 1),
    ):
        try:
            if int(tuning.get(_tkey, DEFAULTS["tuning"][_tkey])) < _minimum:
                errors.append(f"tuning.{_tkey}不能小于{_minimum}")
        except (TypeError, ValueError):
            errors.append(f"tuning.{_tkey}必须是整数")
    try:
        if float(tuning.get("adaptive_cooldown_seconds", DEFAULTS["tuning"]["adaptive_cooldown_seconds"])) < 0:
            errors.append("tuning.adaptive_cooldown_seconds不能为负数")
    except (TypeError, ValueError):
        errors.append("tuning.adaptive_cooldown_seconds必须是数值")
    try:
        poll_min = float(tuning.get("run_control_poll_min_seconds", DEFAULTS["tuning"]["run_control_poll_min_seconds"]))
        poll_max = float(tuning.get("run_control_poll_max_seconds", DEFAULTS["tuning"]["run_control_poll_max_seconds"]))
        if poll_min < 0 or poll_max < poll_min:
            errors.append("tuning.run_control_poll 区间非法（min >= 0 且 min <= max）")
    except (TypeError, ValueError):
        errors.append("tuning.run_control_poll 必须是数值")
    if config.section("browser").get("selenium_fallback_engine") not in {"none", "playwright"}:
        errors.append("browser.selenium_fallback_engine只能是none或playwright")
    if config.source_kind == "browser" and not config.section("browser").get("engine"):
        errors.append("browser.engine不能为空")
    # B5：隐身分级契约——非法值直接报错（确定性判据），运行期不做静默回退
    _stealth_raw = str(config.section("browser").get("stealth_level", "low")).strip().lower()
    if _stealth_raw not in {"off", "low", "medium", "high"}:
        errors.append(
            f"browser.stealth_level 非法: {_stealth_raw!r}（允许 off/low/medium/high）"
        )
    # 分页形状由 core.pagination 的契约统一判定与校验（唯一真源）；
    # 这里只负责把结果并入 errors —— 此前核心只校验 type=page，
    # 游标配置缺 next_path 时既不报错也不翻页（静默少采几页）。
    _source_section = config.section("source")
    errors.extend(validate_pagination(_source_section.get("pagination", {})))
    # ★ 跨字段判据（2026-09-30，**这条有据**）：`location: body` 时 `source.payload` 必须能被
    #   运行时的 `dict()` 消费。判据**只定义一处**＝契约层 `body_location_payload_error`
    #   （与 `validate_pagination` 同一模式），此处只负责把它并入 errors ⇒ CLI 与 GUI 共用。
    #   背景：本轮曾先加过一条**假判据**（「body 必须有 payload」）被 CI 证伪；本条是**实测**出来的
    #   真实前置（`dict("abc")` 必抛、`payload:` 显式 null 也必抛）。
    _pagination_section = _source_section.get("pagination")
    if isinstance(_pagination_section, Mapping):
        _location = str(_pagination_section.get("location") or "").strip().lower()
        if _location == "body":
            _payload_issue = body_location_payload_error(_source_section.get("payload"))
            if _payload_issue:
                errors.append(_payload_issue)
    # ★ 2026-09-30 记录（**刻意不加**跨字段判据，并附推翻它的证据）：
    #   本轮曾一度认为「`location: body` 必须有 `source.payload`」，实测**不成立**：
    #   运行时 `sources.py::GenericSource.seed` 写的是
    #       if template.method == "POST" and pagination.get("location") == "body":
    #           payload = dict(self.source.get("payload", {})); payload[name] = page
    #   ① `payload` **可为空**（缺省 `{}`，页码照样进请求体）；
    #   ② 真正的开关是**该种子请求的 method == POST**；不是 POST 时这一支不走，页码照旧
    #      拼到 URL query（`_with_query`）—— **分页仍然工作**，只是位置不是 body；
    #   ③ `payload` 是**映射**（如 `{"size": 10}`）而非只字符串 ⇒ 按「字符串非空」判它会
    #      **假红**（CI 已实证：`tests/integration/browser/test_sources.py` 因此被误杀）。
    #   契约层立场一致：`validate_pagination({"type":"page","parameter":"p",
    #   "location":"body"})` 本就返回 `[]`（`tests/unit/core/test_pagination_contract.py`）。
    #   ⇒ 不在此处发明前置条件；**真实前置写在 GUI 字段标签与 `docs/CONFIG_REFERENCE.md` 里**。
    fields = config.section("extract").get("fields", {})
    review_policy = config.section("extract").get("review_policy", "recall")
    if not isinstance(review_policy, str) or review_policy not in {"recall", "contract"}:
        errors.append("extract.review_policy必须是recall或contract")
    if fields and not isinstance(fields, dict):
        errors.append("extract.fields必须是字段名到规则的映射")
    elif isinstance(fields, dict) and str(config.section("extract").get("mode", "")) == "json":
        # ★ 2026-11-28：JSON 模式的字段路径**在这里**校验，不要留到采集时。
        # 此前 `path: $..title` 能通过 validate，直到跑采集才抛 ValueError。
        errors.extend(_validate_json_field_paths(fields))
    elif isinstance(fields, dict) and str(config.section("extract").get("mode", "")) == "html":
        # ★ 2026-11-28：CSS 选择器语法同样在**配置校验**阶段检查。
        # 此前 `div[[[[broken` 能通过 validate，直到采集才抛 SelectorSyntaxError。
        errors.extend(_validate_css_field_selectors(fields))
    if isinstance(fields, dict):
        # ★ 2026-11-28：正则语法与 mode 无关（HTML 的 selector 也能配 regex），
        # 所以单独校验，不放进上面那两个 mode 分支。
        errors.extend(_validate_field_regexes(fields))
    deduplicate_by = config.section("extract").get("deduplicate_by", [])
    if not isinstance(deduplicate_by, list) or not all(isinstance(name, str) and name.strip() for name in deduplicate_by):
        errors.append("extract.deduplicate_by必须是非空字段名的数组")
    elif isinstance(fields, dict) and any(name not in fields for name in deduplicate_by):
        errors.append("extract.deduplicate_by引用了未配置的字段")
    follow_xpath = config.section("source").get("follow_xpath", "")
    if not isinstance(follow_xpath, str):
        errors.append("source.follow_xpath必须是XPath字符串")
    elif follow_xpath.strip():
        # ★ 2026-11-28：语法错误在这里报，不要留到链接发现阶段。
        #
        #   但 `core` 必须**import-safe**（`check_architecture` 门禁）：不能依赖
        #   lxml（它是可选依赖）。所以真正编译 XPath 的能力由 html 层
        #   **注册**进来 —— `core` 只留一个钩子，没注册就跳过。
        # ★ 2026-11-28：语法错误在**配置校验**阶段报，不要留到链接发现阶段。
        #
        #   判定放在 `sources/xpath_syntax.py`（叶子模块）：需要 lxml，而 `core`
        #   不能 import 可选依赖（`check_architecture` 判红），也不能 import
        #   `sources/sources.py`（会成环）。叶子模块两个问题都避开。
        from ..sources.xpath_syntax import xpath_syntax_errors

        errors.extend(xpath_syntax_errors(follow_xpath, "source.follow_xpath"))
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
    from .workspace_paths import declared_paths
    try:
        declared_paths(config.raw)
    except ValueError as exc:
        errors.append(str(exc))
    notice = config.section("updates").get("notifications", {})
    if not isinstance(notice, dict):
        errors.append("updates.notifications必须是对象")
    else:
        from ..quality.notification_rules import validate_policy
        errors.extend(validate_policy(notice.get("policy", {})))
        if type(notice.get("enabled", False)) is not bool:
            errors.append("updates.notifications.enabled必须为布尔值")
        if type(notice.get("include_removed", False)) is not bool:
            errors.append("updates.notifications.include_removed必须为布尔值")
        if notice.get("include_removed") is True and (
            not config.section("project").get("task_id") or not config.section("updates").get("identity_fields")
        ):
            errors.append("删除通知需要稳定task_id和显式identity_fields")
        endpoint = notice.get("webhook_url", "")
        token_ref = notice.get("webhook_token_ref", "")
        if not isinstance(endpoint, str) or not isinstance(token_ref, str):
            errors.append("通知URL和凭据引用必须是字符串")
        else:
            if endpoint:
                from urllib.parse import urlsplit
                try:
                    parsed_notice = urlsplit(endpoint)
                    if (parsed_notice.scheme not in {"http", "https"} or not parsed_notice.hostname
                            or parsed_notice.username or parsed_notice.password or parsed_notice.fragment):
                        errors.append("通知Webhook需要不含明文凭据的HTTP(S) URL")
                except ValueError:
                    errors.append("通知Webhook URL无效")
            if token_ref and not token_ref.startswith("secret://"):
                errors.append("通知凭据必须使用secret://引用")
            if notice.get("enabled") is True and not endpoint:
                errors.append("启用记录通知需要webhook_url")
    for key in ("identity_fields", "ignored_fields"):
        value = config.section("updates").get(key, [])
        if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value) or len(set(value)) != len(value):
            errors.append(f"updates.{key}须为不重复的非空字段名列表")
    resources = config.section("resources")
    if str(resources.get("profile", "balanced")).casefold() not in {
        "economy", "balanced", "performance"
    }:
        errors.append("resources.profile只能是economy、balanced或performance")
    try:
        for key in ("minimum_free_disk_bytes", "maximum_workspace_bytes", "maximum_process_tree_bytes"):
            if int(resources.get(key, 0)) < 0:
                errors.append(f"resources.{key}不能为负数")
        for key in ("maximum_runtime_seconds", "check_interval_seconds"):
            if float(resources.get(key, 0)) < 0:
                errors.append(f"resources.{key}不能为负数")
    except (TypeError, ValueError):
        errors.append("resources配置必须是数值")
    if type(resources.get("adaptive_concurrency", True)) is not bool:
        errors.append("resources.adaptive_concurrency必须是布尔值")
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
        ocr_component = pdf.get("ocr_component", "")
        if ocr_component and (not isinstance(ocr_component, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", ocr_component)):
            errors.append("processors.pdf.ocr_component必须是组件标识，不能包含路径")
        if ocr_component and backend not in {"paddle", "tesseract"}:
            errors.append("指定ocr_component时需选择paddle或tesseract后端")
        if backend not in {"none", "paddle", "tesseract", "auto"}:
            errors.append("processors.pdf.ocr_backend只能是none、paddle、tesseract或auto")
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
