"""OmniCrawler 核心配置默认值字典与支持的数据源类型。"""

from __future__ import annotations

from typing import Any

from .builtin_references import DEFAULT_PDF_TEMPLATE
from .migrations import CURRENT_CONFIG_VERSION
from .utils import user_agent

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
    "source": {"kind": "static_html", "seeds": [], "auth_check": {}},
    "crawl": {
        "strategy": "bfs", "max_pages": 100, "max_depth": 3,
        "same_host": True, "allow_domains": [], "deny_patterns": [],
        "allow_patterns": [], "focus_keywords": [], "concurrency": 4,
        # ★ 内置写操作语义 URL 拦截的**显式放行**表（正则）。留空＝完全启用内置保护
        #   （见 security/policy.py 的 _BUILTIN_WRITE_GUARD_PATTERNS）：带「加入购物车 / 下单 /
        #   登出 / 删除」语义的 URL 默认不跟随，避免对第三方产生真实副作用（2026-09-18 走查 R1.4）。
        "allow_write_patterns": [],
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
        # P2-5：关闭 TLS 校验时必须点名的免校验主机（作用域收紧，见 security/tls_scope.py）。
        "tls_insecure_domains": [],
        "allow_private_network": False, "resolve_dns": True,
        "dns_fail_closed": True, "dns_cache_ttl_seconds": 60,
        "headers": {}, "proxy": "",
        # ★ 2026-11-27：连接复用（keep-alive）。**默认关闭** ——
        #   开启后走自建连接（仍固定到已批准 IP），每页省一次 DNS+TCP+TLS 建连。
        #   实测 10 页 14.0s → 3.7s（3.7x）。只对幂等 GET/HEAD 生效。
        "keep_alive": False,
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
    # A-P2-2：审计哈希链配置。strict_chain=True 时重放校验失败 fail-closed 抛错；
    # chain_path 相对项目工作区解析，默认落 logs/audit-chain.jsonl。
    "audit": {
        "chain_enabled": True,
        "strict_chain": False,
        "chain_path": "logs/audit-chain.jsonl",
    },
    # A-P3-5：自适应 / 限流 / 复核权重等阈值集中口径（默认值 + 校验区间见 validate_config）。
    "tuning": {
        # 复核风险评分权重（review/review_workbench.py risk_score），均为非负数值。
        "review_weights": {
            "missing_required": 30, "rule_conflict": 20, "ai_conflict": 15,
            "structure_drift": 20, "ocr_quality": 20, "duplicate": 10, "pending_deletion": 25,
        },
        "adaptive_window": 30,
        "adaptive_audit_limit": 200,
        "adaptive_cooldown_seconds": 10,
        "live_history_window": 16,
        "live_audit_limit": 200,
        "live_domain_capacity": 1000,
        "run_control_poll_min_seconds": 0.05,
        "run_control_poll_max_seconds": 1.0,
    },
    "browser": {
        "engine": "playwright", "headless": True, "pool_size": 2, "actions": [],
        # **内核族**（不是驱动名）：用户选的「当前内核」持久化在这里。
        # 空串 = auto（按 `capability_slots` 的优先级自动选）。
        #
        # 与 `engine` 的区别：`engine` 是驱动（playwright / selenium），
        # `kernel_family` 是同一驱动下的引擎（chromium / firefox / webkit）。
        # 混用会让「换内核」变成「换驱动」，那是两件事。
        "kernel_family": "",
        "selenium_fallback_engine": "none",
        "capture_api_responses": True, "max_api_response_bytes": 1_000_000,
        "max_api_capture_bytes": 10_000_000,
        "auto_generate_api_templates": True,
        # B5：隐身分级接线（off=不注入；low=stealth.min.js+webdriver 隐藏，历史默认；
        # medium/high=叠加 stealth_enhanced 分级指纹脚本）。UA 不参与随机化（诚实自报铁则）。
        "stealth_level": "low",
    },
    # U1/U3（§11.1）：storage_state 快照与 HTTP cookie jar 的路径规则见
    # fetching/session_state.py。bridge_to_http 默认开启（用户手动登录一次后
    # 应当被 HTTP 引擎直接复用），关闭只影响之后的同步、不删除已存会话。
    "session": {"persist_cookies": False, "name": "default", "bridge_to_http": True},
    "auth": {"provider": "", "options": {}},
    "extract": {
        "mode": "auto", "parser": "", "extractor": "", "item_selector": "", "fields": {},
        "quality_threshold": 0.8, "review_low_confidence": True, "review_policy": "recall",
    },
    "data_quality": {
        "entity_fields": [],
        "entity_resolution": {"aliases": {}, "csv": ""},
        # ★ 空列表 = 「用记录里实际存在的非空文本字段」（见 data_intelligence._effective_dedup_fields）。
        #   旧默认是英文硬编码 ["title", "text"]，而本项目分析器产出的字段名是中文（标题 / 正文 / …）
        #   ⇒ 取不到任何值 ⇒ 一条记录都不进入 simhash ⇒ near_duplicates 恒为 0，
        #   而质量报告照样给 average_quality_score: 1.0（2026-09-18 走查 R1.1。
        #   即「判据从未被调用」被误读成「没有重复」——度量失败伪装成业务结论）。
        "near_duplicate_fields": [],
        "near_duplicate_hamming": 3,
        "near_duplicate_max_records": 5000,
    },
    "transformers": [],
    "download": {
        "enabled": False,
        "verified_pdf_manifest": False,
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
        "identity_fields": [],
        "notifications": {"enabled": False, "include_removed": False, "webhook_url": "", "webhook_token_ref": ""},
        "ignored_fields": ["fetched_at", "updated_at", "crawl_time", "timestamp"],
        "detect_same_url_changes": True, "keep_versions": True,
        # ★★ `confirm_missing_runs` 已**删除**（2026-09-30 用户拍板）。
        #   它从 v1.1.0（初始历史）起就**只有默认值与校验、零消费点** —— 连它引用的测试文件
        #   (`test_v110_features.py`) 都全仓不存在 ⇒ "删除需连续 N 次确认"这条不变量**从未实现**。
        #   实际保护走另一条路：`review/run_compare.compare_runs`（后一次运行**完成**才确认
        #   `removed`，未完成则降级 `possibly_removed`）。
        #   ★ 删除而不是保留的理由：**一个不生效的开关会误导**（把它 2→5 会让人以为更保守）。
        #   本仓已把"定义但零调用点"当明确缺陷类型（`yaml_editor` 的 `REQUIRED_TOP_KEYS` 即先例）。
        #   若你的配置里仍有它，会收到一条**未知字段警告**（不是错误）——删掉即可。
    },
    # 应用自更新（issue #88）：检查新版本 + 应用**已签名**升级包（stage/apply/回滚）。
    # ★ 与上面的 `updates` 段**无关**（那是网站变更监控），故另立一段，别混。
    # ★ 两条红线：**不设默认信任根**、**不设「跳过验签」开关**——缺任一配置即视为禁用，
    #   不会"降级为不校验"（§10.7 安全不降级）。
    "self_update": {
        # 更新源基址：远程目录（https://…）或本地目录，内含 update.json 与各平台资产。
        # 留空 = 禁用。**不指向任何特定托管方**（§10.7 不做「core 硬编码 GitHub API 调用」），
        # 本地目录形态同时让离线/内网镜像开箱可用。
        "feed_url": "",
        # ed25519 信任根公钥（base64 编码的 32 字节；也接受 "hex:" 前缀的十六进制）。留空 = 禁用。
        # 与 `services/updater.UpgradeManager` / `services/component_manager` 同一信任根语义。
        "trusted_public_key": "",
        # 资产键的版本后缀：Standard / Full（对齐发布资产命名
        # `OmniCrawler-<ver>-<OS>-Portable-<Edition>`，键的完整形态为 `<platform>-<edition>`）。
        "edition": "Standard",
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
            "ocr_component": "",
        }
    },
    "outputs": {
        "jsonl": True, "csv": True, "xlsx": True, "parquet": False, "duckdb": False,
        "exporter": "default", "plugin_exporters": [], "ai_act_summary": False,
    },
    "storage": {
        "objects": {"backend": "local", "local_directory": "."},
        "records": {"backends": [], "fail_open": False, "max_errors": 200},
        "retention": {"raw_days": None, "artifacts_days": None, "diagnostics_days": None},
    },
    "plugins": {
        "workspace_paths": [],
        "paths": ["plugins/", "plugins_installed/"], "allow_external_paths": False, "fail_open": False,
        "hook_fail_open": False,
        # 旧版全局权限池仅用于单插件迁移兼容；新授权必须绑定插件与载荷哈希。
        "approved_permissions": [],
        "permission_grants": {},
        # None 保持旧项目“已安装即启用”；GUI 首次管理市场插件后写入显式 ID 列表。
        "enabled_market_plugins": None,
        "trust_public_key": "",
        # 签名策略：strict（默认）= 未签名拒载、本地创作者签名经信任询问确认；
        # developer = 未验签/未信任作者时警告放行（仅测试与本地开发）。
        # 市场目录（plugins_installed/）的插件始终要求维护者签名（信任根）。
        "signature_policy": "strict",
        # AST 静态检查豁免列表（加载前拦截 subprocess/ctypes/eval/os.system 等危险模式）。
        # 与插件文件内 "# omnicrawler: allow-ast <名称>" 注释等效；豁免必须显式、可审计。
        "ast_allowed_patterns": [],
        # OmniCrawler 市场 catalog 基址（单一可迁移配置）。所有 catalog 内部路径均为相对此基址
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
        "adaptive_concurrency": True,
        "maximum_process_tree_bytes": 0,
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

