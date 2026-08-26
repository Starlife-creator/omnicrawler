"""Site Categorizer + 模板推荐引擎（三层漏斗：L1 硬止损 → L2 本地映射 → L3 受限嗅探）。

执行顺序严格：L1 硬止损 → L2 本地 YAML 映射 → L3 受限嗅探 → generic_html 兜底，
任意一层命中绝不进入下一层。L1/L2 纯内存零网络；L3 默认关闭（enable_sniffing=false），
开启时必须注入经 EgressBroker 审计的**同步** fetcher，串行执行、HEAD+Range:0-8192、
严格超时（FINAL-U6：本地 future 截断，默认 2s；AsyncFetcher 不受支持且被显式拒绝）。
人工确认闸门由 CLI/GUI 侧负责，本模块只产出「推荐 + 置信度 + 命中来源」结构化结果。
"""

from __future__ import annotations

import logging
import re
import threading
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from ..templates.template_catalog import TemplateCatalog
from .site_aliases import normalize_host

log = logging.getLogger(__name__)


# ── 公开数据结构 ────────────────────────────────────────────────
_HIT_SOURCE_L1 = "l1_stoploss"
_HIT_SOURCE_L2 = "l2_mapping"
_HIT_SOURCE_L3 = "l3_sniff"  # enable_sniffing 开启且注入 fetcher 时启用
_HIT_SOURCE_FALLBACK = "generic_fallback"

_FINAL_FALLBACK_TEMPLATE = "builtin:generic/list_detail.yaml"


@dataclass(frozen=True, slots=True)
class CategorizeResult:
    """单条 URL 的分类推荐结果（由人工闸门在 wizard 内确认后再应用）。"""

    url: str
    template_id: str                       # 最终推荐模板（经 fallback_mapping 兜底）
    confidence: float                      # 0.30-1.00
    hit_source: str                        # 命中来源（_HIT_SOURCE_* 常量）
    raw_requested_template: str            # L1 规则/L2 YAML 原始写的模板 ID（未 fallback 前）
    fallback_used: bool                    # 是否触发了大类兜底（模板不存在时）
    reason: str = ""                       # 人读命中说明（如「L1: 二进制扩展名 .pdf」）
    eTLD1: str = ""                        # 调试用：抽取后的主域名


@dataclass(frozen=True, slots=True)
class CategorizeSummary:
    """批量 classify 的命中率统计（doctor 预检消费）。"""

    total: int
    l1: int
    l2: int
    l3: int
    generic: int
    fallback_used: int
    hits_counter: dict[str, int]            # hit_source 计数（dict，便于 JSON）
    per_url: tuple[CategorizeResult, ...]


# ── L1：硬止损禁区过滤器（精确正则 + eTLD+1 集合，零网络） ──────

# 扩展名精确锚定末尾（严格 fullmatch，无 .* 回溯）
_L1_BINARY_EXT_RE = re.compile(
    r".*\.(pdf|xbrl|docx?|xlsx?|pptx?|zip|rar|7z|tar\.gz|tgz|bz2|xz|parquet)$",
    re.IGNORECASE,
)
_L1_VIDEO_EXT_RE = re.compile(
    # FINAL-D1：不含裸 `ts`——TypeScript 源码会被以 confidence=1.0 误判为视频；
    # HLS 场景已由 m3u8 覆盖，MPEG-TS 直链段由 L3 Content-Type(video/) 规则兜底。
    r".*\.(mp4|m4v|webm|m3u8|mov|avi|flv)$",
    re.IGNORECASE,
)
_L1_API_EXT_RE = re.compile(r".*\.(json|geojson|ndjson)$", re.IGNORECASE)
# 路径段（锚定段名精确包含，不模糊子串）
_L1_API_PATH_RE = re.compile(
    r"(^|/)(api|rest|graphql|v\d+|openapi|json)(/|$)",
    re.IGNORECASE,
)
# releases/files/file/d：精确「路径段」匹配（/releases/ 出现在路径段列表里才算）
_L1_PORTAL_PATH_SEGMENTS = frozenset({"releases", "files", "file", "d"})


# 金融/政府权威域：eTLD+1 精确集合（绝不子串包含，避免 evil-gov-scam.com 误判）
_L1_FINANCIAL_REGULATOR_DOMAINS = frozenset({
    "sec.gov",
    "sec.gov.cn",
    "cninfo.com.cn",
    "sse.com.cn",
    "szse.cn",
    "hkexnews.hk",
    "mops.gov.cn",
    "csrc.gov.cn",
    "chinabond.com.cn",
    "chinaclear.cn",
    "amac.org.cn",
})
_L1_GOV_EDU_MIL_SUFFIXES = (
    ".gov.cn", ".gov", ".edu.cn", ".edu", ".mil.cn", ".mil",
    ".ac.cn",
)
# 云存储 eTLD+1 精确集合（+ 前缀白名单）
_L1_CLOUD_STORAGE_DOMAINS = frozenset({
    "s3.amazonaws.com",
    "aliyuncs.com",
    "blob.core.windows.net",
    "qiniucdn.com",
    "upaiyun.com",
    "qbox.me",
    "ufileos.com",
})


# 模板 ID 常量（L1 强制锁定）—— 都指向 catalog 真实存在的大类 builtin 模板
_T_BINARY = "builtin:documents/office_collection.yaml"
_T_VIDEO = "builtin:generic/media_gallery.yaml"
_T_API = "builtin:protocols/rest_offset.yaml"
_T_FINANCIAL = "builtin:industries/financial_disclosures.yaml"
_T_GOV = "builtin:industries/government_policy.yaml"
_T_CLOUD = "builtin:documents/archive_collection.yaml"
_T_PORTAL = "builtin:documents/archive_collection.yaml"

log = logging.getLogger(__name__)


# ── L3：受限轻量嗅探（严格 HEAD+Range 0-8192，2s 超时，串行，不跟随 3xx） ──
# 命中后置信度 0.70，L3 绝不跟随 3xx；重定向需要调用方重新把 Location 丢回三层漏斗
_L3_SNIFF_CONFIDENCE = 0.70
# Content-Type → (模板 ID常量, 说明) 映射；只取 type/subtype 前两段，忽略 charset/boundary
_L3_CONTENT_TYPE_RULES: tuple[tuple[str, str, str], ...] = (
    # 二进制文档
    ("application/pdf", _T_BINARY, "L3: Content-Type application/pdf"),
    ("application/vnd.openxmlformats-officedocument", _T_BINARY, "L3: Content-Type OOXML Office 文档"),
    ("application/msword", _T_BINARY, "L3: Content-Type Word .doc"),
    ("application/vnd.ms-excel", _T_BINARY, "L3: Content-Type Excel .xls"),
    ("application/vnd.ms-powerpoint", _T_BINARY, "L3: Content-Type PPT .ppt"),
    ("application/x-tar", _T_BINARY, "L3: Content-Type tar 归档"),
    ("application/zip", _T_BINARY, "L3: Content-Type zip 归档"),
    ("application/x-7z-compressed", _T_BINARY, "L3: Content-Type 7z 归档"),
    ("application/x-rar-compressed", _T_BINARY, "L3: Content-Type rar 归档"),
    ("application/parquet", _T_BINARY, "L3: Content-Type Parquet 列存"),
    # 视频
    ("video/", _T_VIDEO, "L3: Content-Type 视频流"),
    # 结构化 API
    ("application/json", _T_API, "L3: Content-Type application/json"),
    ("application/geo+json", _T_API, "L3: Content-Type GeoJSON"),
    ("application/x-ndjson", _T_API, "L3: Content-Type NDJSON"),
)
_L3_SERVER_HEADER_RULES: tuple[tuple[str, str, str], ...] = (
    ("amazons3", _T_CLOUD, "L3: Server 头 AmazonS3 → 云存储直链"),
    ("aliyunoss", _T_CLOUD, "L3: Server 头 AliyunOSS → 云存储直链"),
    ("windows-azure-storage", _T_CLOUD, "L3: Server 头 Azure Blob → 云存储直链"),
)
_L3_TIMEOUT_S_DEFAULT = 2.0
# L3 嗅探时注入 meta 标签（合规观测审计，可被 EgressBroker 追踪）
_L3_SNIFF_META_TAG = {"__categorizer_sniff": "l3_phase2", "__audit_channel": "categorizer_l3"}


def _l3_deduce_from_headers(status_code: int, headers: dict[str, str]) -> CategorizeResult | None:
    """把嗅探响应头信号 → CategorizeResult。

    返回 None 表示「L3 没嗅出强信号，调用方回退 generic_html」。
    """
    # 3xx：不跟随，返回带 redirect 标记（raw_requested_template=<L3-redirect-required>）
    # 的 result，上层据此识别需要重新进 funnel 的 URL。
    if 300 <= status_code < 400:
        loc = headers.get("location") or headers.get("Location") or ""
        return CategorizeResult(
            url="<placeholder>", template_id=_FINAL_FALLBACK_TEMPLATE,
            confidence=_L3_SNIFF_CONFIDENCE,
            hit_source=_HIT_SOURCE_L3, raw_requested_template="<L3-redirect-required>",
            fallback_used=True,
            reason=(
                f"L3: 收到 3xx Status={status_code} Location={loc!r}，"
                "返回 redirect 标记结果供上层重新入漏斗"
            ),
        )
    ct_raw = headers.get("content-type") or headers.get("Content-Type") or ""
    ct = ct_raw.split(";", 1)[0].strip().lower()
    if ct:
        for prefix, tpl, reason in _L3_CONTENT_TYPE_RULES:
            if ct.startswith(prefix):
                return CategorizeResult(
                    url="<placeholder>", template_id=tpl, confidence=_L3_SNIFF_CONFIDENCE,
                    hit_source=_HIT_SOURCE_L3, raw_requested_template=tpl,
                    fallback_used=False, reason=reason,
                )
    # Content-Disposition: attachment → 下载门户（弱信号，仍用 archive_collection）
    cd_raw = headers.get("content-disposition") or headers.get("Content-Disposition") or ""
    if "attachment" in cd_raw.lower():
        return CategorizeResult(
            url="<placeholder>", template_id=_T_PORTAL, confidence=_L3_SNIFF_CONFIDENCE,
            hit_source=_HIT_SOURCE_L3, raw_requested_template=_T_PORTAL,
            fallback_used=False, reason="L3: Content-Disposition: attachment → 下载直链",
        )
    server_raw = headers.get("server") or headers.get("Server") or ""
    server = server_raw.lower()
    if server:
        for token, tpl, reason in _L3_SERVER_HEADER_RULES:
            if token in server:
                return CategorizeResult(
                    url="<placeholder>", template_id=tpl, confidence=_L3_SNIFF_CONFIDENCE,
                    hit_source=_HIT_SOURCE_L3, raw_requested_template=tpl,
                    fallback_used=False, reason=reason,
                )
    return None


def _safe_urlparse(url: str) -> tuple[str, str, str]:
    """返回 (scheme, host_netloc, path)；无法解析时 ("", "", "")。"""
    try:
        if "://" not in url:
            # 对纯域名输入也兜底能查
            url = "https://" + url.lstrip("/")
        parsed = urlparse(url)
        return parsed.scheme or "", parsed.netloc or "", parsed.path or ""
    except Exception:  # noqa: BLE001
        return "", "", ""


def _etld1_and_suffix_check(host: str) -> tuple[str, str | None]:
    """返回 (主域名 eTLD+1, 如果是 gov/edu 等权威后缀则返回该后缀，否则 None)。"""
    try:
        host = normalize_host(host) or ""
    except Exception:  # noqa: BLE001
        return "", None
    low = host.lower()
    for suf in _L1_GOV_EDU_MIL_SUFFIXES:
        if low.endswith(suf):
            return host, suf
    return host, None


def _try_l1_stoploss(url: str) -> CategorizeResult | None:
    """尝试 L1 硬止损。命中返回 CategorizeResult，未命中返回 None 进 L2。"""
    scheme, host, path = _safe_urlparse(url)
    if not scheme and not host:
        return None
    path_norm = (path or "/").lower()

    # ① 扩展名匹配（最确定，先查）— 用正则捕获组取扩展名，支持 tar.gz 这类多点段
    m = _L1_BINARY_EXT_RE.fullmatch(path_norm)
    if m:
        ext = m.group(1)
        return CategorizeResult(
            url=url, template_id=_T_BINARY, confidence=1.0,
            hit_source=_HIT_SOURCE_L1, raw_requested_template=_T_BINARY,
            fallback_used=False, reason=f"L1: 二进制扩展名 .{ext}",
        )
    m = _L1_VIDEO_EXT_RE.fullmatch(path_norm)
    if m:
        ext = m.group(1)
        return CategorizeResult(
            url=url, template_id=_T_VIDEO, confidence=1.0,
            hit_source=_HIT_SOURCE_L1, raw_requested_template=_T_VIDEO,
            fallback_used=False, reason=f"L1: 视频扩展名 .{ext}",
        )
    m = _L1_API_EXT_RE.fullmatch(path_norm)
    if m:
        ext = m.group(1)
        return CategorizeResult(
            url=url, template_id=_T_API, confidence=1.0,
            hit_source=_HIT_SOURCE_L1, raw_requested_template=_T_API,
            fallback_used=False, reason=f"L1: 结构化数据扩展名 .{ext}",
        )

    # ② 政府/金融权威域（后缀 + 精确 eTLD+1 集合双保险，避免子串误判）
    eTLD1, auth_suf = _etld1_and_suffix_check(host)
    # 统一剥 www. 前缀再比对精确集合（保留原值用于 eTLD1 字段回显）
    eTLD1_stripped = eTLD1[4:] if eTLD1 and eTLD1.lower().startswith("www.") else eTLD1
    eTLD1_norm = (eTLD1_stripped or "").lower()
    if auth_suf is not None:
        # 金融集合里列出来的 → 金融披露；否则 → 政府公文/教育
        if eTLD1_norm and eTLD1_norm in _L1_FINANCIAL_REGULATOR_DOMAINS:
            template = _T_FINANCIAL
            reason = f"L1: 金融监管权威域（精确集合） {eTLD1_stripped}"
        else:
            template = _T_GOV
            reason = f"L1: 政府/教育/军事权威后缀 {auth_suf}"
        return CategorizeResult(
            url=url, template_id=template, confidence=1.0,
            hit_source=_HIT_SOURCE_L1, raw_requested_template=template,
            fallback_used=False, reason=reason, eTLD1=eTLD1,
        )
    if eTLD1_norm and eTLD1_norm in _L1_FINANCIAL_REGULATOR_DOMAINS:
        return CategorizeResult(
            url=url, template_id=_T_FINANCIAL, confidence=1.0,
            hit_source=_HIT_SOURCE_L1, raw_requested_template=_T_FINANCIAL,
            fallback_used=False, reason=f"L1: 金融监管权威域（精确集合） {eTLD1_stripped}",
            eTLD1=eTLD1,
        )

    # ③ 云存储：精确匹配（s3.amazonaws.com）+ 虚拟主机子域匹配（bucket.s3.amazonaws.com）
    #    不使用 PSL，用「精确 ∈ set」或「以 .set_elem 为后缀」双规则，防止 *.scam.com 误匹配
    cloud_base: str | None = None
    if eTLD1_norm:
        if eTLD1_norm in _L1_CLOUD_STORAGE_DOMAINS:
            cloud_base = eTLD1_norm
        else:
            for d in _L1_CLOUD_STORAGE_DOMAINS:
                if eTLD1_norm.endswith("." + d):
                    cloud_base = d
                    break
    if cloud_base is not None:
        display = eTLD1_stripped if eTLD1_stripped else eTLD1
        return CategorizeResult(
            url=url, template_id=_T_CLOUD, confidence=1.0,
            hit_source=_HIT_SOURCE_L1, raw_requested_template=_T_CLOUD,
            fallback_used=False, reason=f"L1: 云存储直链 {display}（base {cloud_base}）",
            eTLD1=eTLD1,
        )

    # ④ API 路径段
    if _L1_API_PATH_RE.search(path_norm):
        m = _L1_API_PATH_RE.search(path_norm)
        marker = m.group(2) if m else "api-segment"
        return CategorizeResult(
            url=url, template_id=_T_API, confidence=1.0,
            hit_source=_HIT_SOURCE_L1, raw_requested_template=_T_API,
            fallback_used=False, reason=f"L1: API 路径段 /{marker}/",
        )

    # ⑤ 下载门户（精确路径段 releases/files/file/d 包含在 segments 中 + host 匹配已知 portal）
    if eTLD1_norm:
        portal_hosts = {"github.com", "sourceforge.net"}
        segs = [s for s in path_norm.split("/") if s]
        seg_set = set(segs)
        if eTLD1_norm in portal_hosts and (seg_set & _L1_PORTAL_PATH_SEGMENTS):
            matched = sorted(seg_set & _L1_PORTAL_PATH_SEGMENTS)[0]
            return CategorizeResult(
                url=url, template_id=_T_PORTAL, confidence=0.99,
                hit_source=_HIT_SOURCE_L1, raw_requested_template=_T_PORTAL,
                fallback_used=False,
                reason=f"L1: 下载门户 {eTLD1_stripped or eTLD1}，路径段 /{matched}/",
                eTLD1=eTLD1,
            )

    # ⑥ Google drive /file/d/ 特殊组合（含子域）
    if host.lower().endswith("drive.google.com") and "/file/d/" in path_norm:
        return CategorizeResult(
            url=url, template_id=_T_PORTAL, confidence=0.99,
            hit_source=_HIT_SOURCE_L1, raw_requested_template=_T_PORTAL,
            fallback_used=False, reason="L1: Google Drive /file/d/ 下载链接",
        )

    return None


# ── L2：本地 YAML 映射 + AppConfig 节内双形态 ─────────────────

_DEFAULT_BUILTIN_YAML_NAME = "b2_domain_mappings_default.yaml"
_PROJECT_REL_YAML = Path("config") / "b2_domain_mappings.yaml"


def _builtin_default_yaml_path() -> Path:
    # 位于 omnicrawler/data/（独立数据包），避免 omnicrawler/config 命名空间遮蔽兼容导入
    return Path(__file__).resolve().parent.parent / "data" / _DEFAULT_BUILTIN_YAML_NAME


@dataclass(slots=True)
class SiteCategorizer:
    """三层漏斗 Site Categorizer（L1+L2 零网络；L3 受限嗅探需显式开启）。"""

    # 映射（eTLD1 精确 key → raw template id）；合并后最终生效字典
    mappings: dict[str, str] = field(default_factory=dict)
    # 当 raw template id 在 catalog 中不存在时，raw → 大类兜底
    fallback_mapping: dict[str, str] = field(default_factory=dict)
    # enable_sniffing：默认 false（安全优先）；开启且注入 fetcher 时才执行 L3 嗅探
    enable_sniffing: bool = False
    # 命中率计数器（类级，多次 classify 累加）
    hits: Counter[str] = field(default_factory=Counter)
    fallback_used_count: int = 0

    _loaded_from: tuple[str, ...] = field(default_factory=tuple)
    _yaml_mtime: float | None = None  # 记录最近一次成功加载 YAML 的 mtime（供轮询热重载）
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _last_error: str | None = None  # 最近一次 reload 的错误（Doctor 预检用）

    # ── 加载 ──────────────────────────────────────────────────
    @classmethod
    def from_app_config(
        cls,
        app_config: Any,
        *,
        project_root: Path | None = None,
        extra_yaml_paths: Iterable[Path] = (),
        refresh: bool = False,
    ) -> SiteCategorizer:
        """按两级合并顺序加载（AppConfig 节内 → 项目级 YAML → 出厂内置 YAML）。"""
        instance = cls()
        instance._reload_inner(
            app_config=app_config, project_root=project_root,
            extra_yaml_paths=list(extra_yaml_paths), force=refresh,
        )
        return instance

    def reload(
        self,
        *,
        app_config: Any = None,
        project_root: Path | None = None,
        extra_yaml_paths: Iterable[Path] = (),
    ) -> tuple[bool, str | None]:
        """热重载（原子：失败保留旧状态）。返回 (ok, last_error_message)。"""
        with self._lock:
            ok, err = self._reload_inner(
                app_config=app_config, project_root=project_root,
                extra_yaml_paths=list(extra_yaml_paths), force=True,
            )
            return ok, err

    def _reload_inner(
        self,
        *,
        app_config: Any,
        project_root: Path | None,
        extra_yaml_paths: list[Path],
        force: bool,
    ) -> tuple[bool, str | None]:
        # 快照旧状态（失败时回滚）
        old_mappings = dict(self.mappings)
        old_fallback = dict(self.fallback_mapping)
        old_loaded = self._loaded_from
        old_sniff = self.enable_sniffing
        old_mtime = self._yaml_mtime
        try:
            merged_mappings: dict[str, str] = {}
            merged_fallback: dict[str, str] = {}
            loaded_sources: list[str] = []

            # ① 最低优先级：出厂内置 YAML（必须存在）
            builtin = _builtin_default_yaml_path()
            if builtin.is_file():
                data = _safe_load_yaml(builtin)
                for k, v in (data.get("mappings") or {}).items():
                    merged_mappings[str(k).strip().lower()] = str(v).strip()
                for k, v in (data.get("fallback_mapping") or {}).items():
                    merged_fallback[str(k).strip()] = str(v).strip()
                loaded_sources.append(f"builtin:{builtin.name}")

            # ② 次高优先级：<project>/config/b2_domain_mappings.yaml
            if project_root is not None:
                proj_yaml = Path(project_root) / _PROJECT_REL_YAML
                if proj_yaml.is_file():
                    data = _safe_load_yaml(proj_yaml)
                    for k, v in (data.get("mappings") or {}).items():
                        merged_mappings[str(k).strip().lower()] = str(v).strip()
                    for k, v in (data.get("fallback_mapping") or {}).items():
                        merged_fallback[str(k).strip()] = str(v).strip()
                    loaded_sources.append(str(proj_yaml))
                    self._yaml_mtime = proj_yaml.stat().st_mtime_ns / 1e9

            # ③ 额外传入的 YAML（自定义 merge，最高优先级）
            for p in extra_yaml_paths:
                p = Path(p)
                if p.is_file():
                    data = _safe_load_yaml(p)
                    for k, v in (data.get("mappings") or {}).items():
                        merged_mappings[str(k).strip().lower()] = str(v).strip()
                    for k, v in (data.get("fallback_mapping") or {}).items():
                        merged_fallback[str(k).strip()] = str(v).strip()
                    loaded_sources.append(str(p))

            # ④ 最高优先级：config.yaml 节内 source.categorizer.mappings
            sniff_cfg: bool = False
            if app_config is not None:
                try:
                    src_section = app_config.section("source") or {}
                    cat_section = src_section.get("categorizer") if isinstance(src_section, dict) else None
                    if isinstance(cat_section, dict):
                        sniff_cfg = bool(cat_section.get("enable_sniffing", False))
                        sect_mappings = cat_section.get("mappings") or {}
                        if isinstance(sect_mappings, dict):
                            for k, v in sect_mappings.items():
                                merged_mappings[str(k).strip().lower()] = str(v).strip()
                        sect_fallback = cat_section.get("fallback_mapping") or {}
                        if isinstance(sect_fallback, dict):
                            for k, v in sect_fallback.items():
                                merged_fallback[str(k).strip()] = str(v).strip()
                except Exception as exc:  # noqa: BLE001
                    raise ValueError(f"config.source.categorizer 解析失败：{exc}") from exc

            # 写入新状态（全部成功后才提交）
            self.mappings = merged_mappings
            self.fallback_mapping = merged_fallback
            self.enable_sniffing = sniff_cfg
            self._loaded_from = tuple(loaded_sources)
            self._last_error = None
            # B05-028：删除 mtime 短路死代码——加载已在此完成，mtime 判断无操作；
            # 热重载由调用方基于 _yaml_mtime 变化主动触发。
            return True, None
        except Exception as exc:  # noqa: BLE001
            # 原子回滚：恢复旧状态
            self.mappings = old_mappings
            self.fallback_mapping = old_fallback
            self._loaded_from = old_loaded
            self.enable_sniffing = old_sniff
            self._yaml_mtime = old_mtime
            err = str(exc)
            self._last_error = err
            log.warning("SiteCategorizer reload 失败，保留上一份有效配置：%s", err)
            return False, err

    def loaded_sources(self) -> tuple[str, ...]:
        return self._loaded_from

    def last_error(self) -> str | None:
        return self._last_error

    # ── 分类主入口 ──────────────────────────────────────────────
    def classify(
        self,
        urls: Iterable[str],
        *,
        catalog: TemplateCatalog | None = None,
        fetcher: Any | None = None,
        l3_timeout_s: float = _L3_TIMEOUT_S_DEFAULT,
    ) -> CategorizeSummary:
        """对一组 URLs 批量分类；catalog 传 None 时不做模板存在性校验。

        Parameters
        ----------
        fetcher: 可选 AsyncFetcher/HTTPXAsyncFetcher 实例（必须已绑定 EgressBroker 审计通道）。
                 仅当 enable_sniffing=True AND fetcher 非空时，才会执行 L3 受限嗅探；
                 L3 串行执行（严格一个接一个），每条 HEAD+Range:0-8192，超时 2s。
        """
        urls_list = [u for u in urls if u]
        total = len(urls_list)
        results: list[CategorizeResult] = []
        l1 = l2 = l3 = generic = 0
        fallback_count = 0
        for u in urls_list:
            r = self._classify_one(u, catalog=catalog, fetcher=fetcher, l3_timeout_s=l3_timeout_s)
            results.append(r)
            if r.fallback_used:
                fallback_count += 1
            if r.hit_source == _HIT_SOURCE_L1:
                l1 += 1
            elif r.hit_source == _HIT_SOURCE_L2:
                l2 += 1
            elif r.hit_source == _HIT_SOURCE_L3:
                l3 += 1
            else:
                generic += 1
        # B05-029：快照与计数更新全部在锁内，避免并发下计数竞态
        with self._lock:
            hits_snapshot = Counter(self.hits)
            hits_snapshot.update(r.hit_source for r in results)
            self.hits = hits_snapshot
            self.fallback_used_count += fallback_count
        return CategorizeSummary(
            total=total, l1=l1, l2=l2, l3=l3, generic=generic,
            fallback_used=fallback_count,
            hits_counter=dict(hits_snapshot),
            per_url=tuple(results),
        )

    # ── 内部工具 ──────────────────────────────────────────────
    def _try_l3_sniff_sync(
        self,
        url: str,
        *,
        fetcher: Any,
        timeout_s: float,
    ) -> CategorizeResult | None:
        """发起一次 L3 受限嗅探请求：HEAD + Range 0-8192，严格超时，异常安全（任何异常返回 None 让调用方兜底）。

        必须确保 fetcher 已绑定 EgressBroker/NetworkTargetPolicy 合规审计通道（上层调用方责任）。
        FINAL-U6：仅支持**同步** fetcher（AsyncFetcher.fetch 返回协程，此处无法
        await，显式拒绝）；严格超时由本地 future 硬性截断实现，不再依赖 fetcher
        自身配置（其默认可达 25s，串行嗅探 N 个 URL 最坏拖 25N 秒）。
        """
        from urllib.parse import urlparse

        from .models import CrawlRequest  # 延迟导入避免循环依赖
        try:
            parsed = urlparse(url)
            if not parsed.scheme or not parsed.netloc:
                return None
            # 构建 CrawlRequest：注入 __categorizer_sniff meta 标签（审计追踪用）+ HEAD + Range 0-8192
            headers: dict[str, str] = {"Range": "bytes=0-8192", "Accept": "*/*"}
            # 如果 fetcher 没有暴露 sync fetch 方法就回 None（避免 AttributeError 中断批处理）
            if not hasattr(fetcher, "fetch") or not callable(getattr(fetcher, "fetch", None)):
                return None
            # FINAL-U6：异步 fetcher 显式拒绝（协程未被 await 时请求根本不会发出）
            fetcher_module = str(type(fetcher).__module__)
            fetcher_name = type(fetcher).__name__
            if fetcher_module.endswith("async_fetcher") or "Async" in fetcher_name:
                log.debug(
                    "L3 受限嗅探需要同步 fetcher，收到 %s（来自 %s），跳过 %s",
                    fetcher_name, fetcher_module, url,
                )
                return None
            req = CrawlRequest(
                url=url,
                method="HEAD",
                headers=headers,
                kind="categorizer_l3_sniff",
                render=False,
                depth=0,
                priority=-1.0,  # 低优先级，不抢占真实抓取队列
            )
            # meta 标签：兼容不同 CrawlRequest 版本（新版有 meta 字段，旧版没有时忽略）
            try:
                if hasattr(req, "meta") and isinstance(req.meta, dict):
                    req.meta.update(_L3_SNIFF_META_TAG)
            except Exception:  # noqa: BLE001
                pass
            # FINAL-U6：兑现「严格超时」承诺——受控线程执行 + future 超时硬截断。
            # 到点即放弃该 URL 嗅探；已启动的后台线程随其自身 I/O 超时自然结束，
            # 结果被丢弃（解释器退出前至多延迟一个 fetcher 默认超时，属可接受代价）。
            import concurrent.futures

            def _do_fetch() -> Any:
                return fetcher.fetch(req)

            pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="omnicrawler-l3-sniff",
            )
            try:
                future = pool.submit(_do_fetch)
                try:
                    result = future.result(timeout=max(0.5, float(timeout_s)))
                except concurrent.futures.TimeoutError:
                    log.debug("L3 受限嗅探超时（%.1fs），放弃 %s", timeout_s, url)
                    return None
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
            # FetchResult 兼容三种形态：status_code 属性 / .status 属性（真实
            # FetchResult 字段）/ dict 键
            status_code: int = 0
            resp_headers: dict[str, str] = {}
            if hasattr(result, "status_code"):
                status_code = int(result.status_code or 0)
            elif hasattr(result, "status"):
                status_code = int(result.status or 0)
            elif isinstance(result, dict):
                status_code = int(result.get("status_code") or 0)
            if hasattr(result, "headers"):
                h = result.headers
                if hasattr(h, "keys"):
                    resp_headers = {str(k): str(v) for k, v in dict(h).items()}
            elif isinstance(result, dict) and isinstance(result.get("headers"), dict):
                resp_headers = {str(k): str(v) for k, v in result["headers"].items()}
            deduced = _l3_deduce_from_headers(status_code, resp_headers)
            if deduced is None:
                return None
            # 把占位符 url/etld1 替换为真实值
            _s, host, _p = _safe_urlparse(url)
            etld1, _ = _etld1_and_suffix_check(host)
            return CategorizeResult(
                url=url, template_id=deduced.template_id, confidence=deduced.confidence,
                hit_source=deduced.hit_source, raw_requested_template=deduced.raw_requested_template,
                fallback_used=deduced.fallback_used, reason=deduced.reason,
                eTLD1=etld1,
            )
        except Exception as exc:  # noqa: BLE001
            # 网络异常/超时/审计拒绝 等都静默吞掉，让上层走 generic_html 兜底（不阻断）
            log.debug("L3 受限嗅探跳过 %s：%s", url, exc)
            return None

    def _classify_one(
        self,
        url: str,
        *,
        catalog: TemplateCatalog | None,
        fetcher: Any | None = None,
        l3_timeout_s: float = _L3_TIMEOUT_S_DEFAULT,
    ) -> CategorizeResult:
        # L1 硬止损（先做，命中立即 return）
        l1 = _try_l1_stoploss(url)
        if l1 is not None:
            return l1
        # L2 本地映射：比对 mappings key 时剥 www.，让 www.zhihu.com 也能命中 zhihu.com
        _scheme, host, _path = _safe_urlparse(url)
        eTLD1, _suf = _etld1_and_suffix_check(host)
        eTLD1_norm = (eTLD1 or "").lower()
        if eTLD1_norm.startswith("www."):
            eTLD1_norm = eTLD1_norm[4:]
        if eTLD1_norm and eTLD1_norm in self.mappings:
            raw = self.mappings[eTLD1_norm]
            resolved, fb = self._resolve_template(raw, catalog=catalog)
            return CategorizeResult(
                url=url, template_id=resolved, confidence=0.95,
                hit_source=_HIT_SOURCE_L2, raw_requested_template=raw,
                fallback_used=fb, reason=f"L2: 本地映射 {eTLD1} → {raw}",
                eTLD1=eTLD1,
            )
        # L3 受限嗅探（enable_sniffing=true AND fetcher 已传入才尝试）
        if self.enable_sniffing:
            if fetcher is not None:
                sniffed = self._try_l3_sniff_sync(url, fetcher=fetcher, timeout_s=l3_timeout_s)
                if sniffed is not None:
                    # 如果 L3 嗅出来的 raw_requested_template 命中了 catalog/fallback_mapping 兜底规则则走一遍
                    raw = sniffed.raw_requested_template
                    resolved, fb_used = self._resolve_template(raw, catalog=catalog)
                    # 当模板存在性校验有兜底时，保持 confidence 不变（L3 原始置信度是信号强度不是模板强度）
                    # B05-030：raw 为 3xx redirect 标记时，此处解析为兜底模板（降级路径），
                    # reason 保留 sniffed.reason 描述重定向来源，供上层区分触发场景。
                    return CategorizeResult(
                        url=sniffed.url, template_id=resolved, confidence=sniffed.confidence,
                        hit_source=sniffed.hit_source, raw_requested_template=raw,
                        fallback_used=sniffed.fallback_used or fb_used,
                        reason=sniffed.reason, eTLD1=sniffed.eTLD1,
                    )
                # fetcher 存在但没嗅出强信号 → 仍走 generic_html（reason 写 L3 尝试过）
                return CategorizeResult(
                    url=url, template_id=_FINAL_FALLBACK_TEMPLATE, confidence=0.30,
                    hit_source=_HIT_SOURCE_FALLBACK, raw_requested_template="<L3-no-signal>",
                    fallback_used=True,
                    reason="L3 已执行受限嗅探（HEAD+Range 0-8192），但未检出强 Content-Type/Server 信号，已降级通用模板 generic_html",
                    eTLD1=eTLD1,
                )
            # enable_sniffing=true 但调用方没传 fetcher → 提醒需注入 fetcher
            return CategorizeResult(
                url=url, template_id=_FINAL_FALLBACK_TEMPLATE, confidence=0.30,
                hit_source=_HIT_SOURCE_FALLBACK, raw_requested_template="<L3-no-fetcher>",
                fallback_used=True,
                reason="L3 嗅探开关已启用但调用方未传入 fetcher 实例，请把 AsyncFetcher 传给 classify(fetcher=...)；当前已降级 generic_html",
                eTLD1=eTLD1,
            )
        # 最终兜底
        return CategorizeResult(
            url=url, template_id=_FINAL_FALLBACK_TEMPLATE, confidence=0.30,
            hit_source=_HIT_SOURCE_FALLBACK,
            raw_requested_template=_FINAL_FALLBACK_TEMPLATE,
            fallback_used=False, reason="未命中 L1/L2，使用通用 HTML 模板兜底",
            eTLD1=eTLD1,
        )

    def _resolve_template(self, raw_id: str, *, catalog: TemplateCatalog | None) -> tuple[str, bool]:
        """解析模板 ID → 存在则直接用；不存在按 fallback_mapping → 最终 generic。"""
        if catalog is None:
            # 无 catalog：信任 L2 用户 YAML；fallback_mapping 仍按规则走以避免下游失败（只是没校验 catalog 存不存在）
            # 若 raw_id 存在于 fallback_mapping keys 中 → 也用原值，只有 catalog 查不到时才兜底
            return raw_id, False
        if catalog.get(raw_id) is not None:
            return raw_id, False
        # catalog 中找不到 raw_id → 走 fallback_mapping
        if raw_id in self.fallback_mapping:
            fb_id = self.fallback_mapping[raw_id]
            if catalog.get(fb_id) is not None:
                return fb_id, True
        # fallback_mapping 也没命中/命中仍不存在 → 最终 FINAL_FALLBACK（builtin:generic/list_detail.yaml 一定存在）
        log.warning("模板 %r 不存在且 fallback_mapping 未命中，已降级为 %r", raw_id, _FINAL_FALLBACK_TEMPLATE)
        return _FINAL_FALLBACK_TEMPLATE, True


def _safe_load_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"无法读取 YAML {path}: {exc}") from exc
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML 语法错误 {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"YAML 顶层必须为 mapping 字典：{path}")
    return data


# ── 人工确认闸门：Template Recommendation ConfirmationEngine ────────

@dataclass(frozen=True, slots=True)
class ConfirmationDecision:
    """单条分类推荐是 auto-approved 还是需要人工闸门。"""

    auto_approved: bool
    approved_reason: str = ""            # auto 时填写，否则空
    human_hint: str = ""                 # 需要人工时说明「为什么需要看一眼」
    threshold_used: float = 0.0


@dataclass(frozen=True, slots=True)
class ConfirmationSummary:
    """把 CategorizeSummary 按「自动 / 人工」分层后交给 CLI/GUI 消费的结构化总览。"""

    total: int
    auto_approved: int
    require_human_review: int
    hits_by_source: dict[str, int]
    auto_rows: tuple[tuple[CategorizeResult, ConfirmationDecision], ...]
    human_rows: tuple[tuple[CategorizeResult, ConfirmationDecision], ...]
    threshold: float
    allow_l1_bypass: bool

    def as_text_table(self, *, max_rows: int = 50) -> str:
        """CLI / 日志 / GUI 文本预览渲染（max_rows 限制避免大批次卡 UI）。"""
        lines: list[str] = []
        lines.append(
            f"# 模板推荐闸门  自动 {self.auto_approved}/{self.total}  "
            f"待人工确认 {self.require_human_review}/{self.total}  "
            f"自动放行阈值 confidence≥{self.threshold:.2f}（L1 永远自动）"
        )
        lines.append(
            "# 命中来源统计: "
            + ", ".join(f"{src}={n}" for src, n in sorted(self.hits_by_source.items()))
        )
        # Auto-approved（只展示前 N 条，节省空间）
        if self.auto_rows:
            lines.append("\n## 已自动放行")
            header = f"  {'#':>3}  {'URL':50}  {'Conf':>5}  {'Source':>13}  Template ID  Reason"
            lines.append(header)
            for i, (r, _d) in enumerate(self.auto_rows[:max_rows], 1):
                url = r.url if len(r.url) <= 50 else (r.url[:47] + "...")
                lines.append(
                    f"  {i:>3}  {url:50}  {r.confidence:4.2f}  {r.hit_source:>13}  "
                    f"{r.template_id}  {r.reason}"
                )
            if len(self.auto_rows) > max_rows:
                lines.append(f"  ... 其余 {len(self.auto_rows) - max_rows} 条已自动放行（省略）")
        if self.human_rows:
            lines.append("\n## ⚠ 需要人工确认")
            header = f"  {'#':>3}  {'URL':50}  {'Conf':>5}  {'Source':>13}  提示  Template ID  Reason"
            lines.append(header)
            for i, (r, d) in enumerate(self.human_rows[:max_rows], 1):
                url = r.url if len(r.url) <= 50 else (r.url[:47] + "...")
                lines.append(
                    f"  {i:>3}  {url:50}  {r.confidence:4.2f}  {r.hit_source:>13}  "
                    f"{d.human_hint}  {r.template_id}  {r.reason}"
                )
            if len(self.human_rows) > max_rows:
                lines.append(f"  ... 其余 {len(self.human_rows) - max_rows} 条等待人工确认（省略）")
        return "\n".join(lines)


class RecommendationConfirmationEngine:
    """B-2 推荐 → 「自动放行 / 人工闸门」分流器。

    设计原则（对应 project_memory 的「人工批准不应成为进步限制」那条）：
    1. L1（confidence=1.00 精确硬止损规则）默认永远自动放行（allow_l1_bypass=True），绝不浪费人点鼠标
    2. L2（≥ 0.95 出厂映射）默认也自动放行——毕竟是用户/维护者手写进 YAML 的精确映射，除非显式 threshold 设到 0.99
    3. L3 + generic 必须人工过一眼（置信度 < 0.85 时 `human_hint` 自动填说明）
    4. fallback_used=True 的项即使阈值够也强制要求人工（是「大类兜底」，不是精确命中）
    """

    DEFAULT_THRESHOLD: float = 0.85

    def __init__(
        self,
        *,
        auto_threshold: float = DEFAULT_THRESHOLD,
        allow_l1_bypass: bool = True,
    ) -> None:
        if not (0.0 <= float(auto_threshold) <= 1.0):
            raise ValueError(f"auto_threshold 必须 ∈ [0, 1]，实际 {auto_threshold!r}")
        self._threshold = float(auto_threshold)
        self._l1_bypass = bool(allow_l1_bypass)

    @property
    def threshold(self) -> float:
        return self._threshold

    def decide(self, r: CategorizeResult) -> ConfirmationDecision:
        """单条结果 → 闸门判定。"""
        # L1（精确正则 / 精确权威域 / 精确云存储集合命中）永远自动放行，不看 threshold
        if self._l1_bypass and r.hit_source == _HIT_SOURCE_L1:
            return ConfirmationDecision(
                auto_approved=True,
                approved_reason=f"L1 精确硬止损（置信度 {r.confidence:.2f}），允许自动",
                threshold_used=self._threshold,
            )
        # Fallback 兜底（模板不存在 / 未命中任何规则走 generic）：必须人工
        if r.fallback_used:
            return ConfirmationDecision(
                auto_approved=False,
                human_hint="触发了 fallback_mapping 兜底（模板不存在或完全没命中规则），请人工确认模板匹配度",
                threshold_used=self._threshold,
            )
        # Confidence ≥ threshold：自动
        if r.confidence >= self._threshold:
            return ConfirmationDecision(
                auto_approved=True,
                approved_reason=f"置信度 {r.confidence:.2f} ≥ 阈值 {self._threshold:.2f}",
                threshold_used=self._threshold,
            )
        # 其余：人工
        return ConfirmationDecision(
            auto_approved=False,
            human_hint=(
                f"置信度 {r.confidence:.2f} < 自动放行阈值 {self._threshold:.2f}；"
                + ("L3 嗅探信号偏弱，建议人工核对接入类型" if r.hit_source == _HIT_SOURCE_L3 else "建议核对推荐模板是否匹配目标站点结构")
            ),
            threshold_used=self._threshold,
        )

    def process(self, s: CategorizeSummary) -> ConfirmationSummary:
        auto_rows: list[tuple[CategorizeResult, ConfirmationDecision]] = []
        human_rows: list[tuple[CategorizeResult, ConfirmationDecision]] = []
        for r in s.per_url:
            d = self.decide(r)
            if d.auto_approved:
                auto_rows.append((r, d))
            else:
                human_rows.append((r, d))
        return ConfirmationSummary(
            total=s.total,
            auto_approved=len(auto_rows),
            require_human_review=len(human_rows),
            hits_by_source=dict(s.hits_counter),
            auto_rows=tuple(auto_rows),
            human_rows=tuple(human_rows),
            threshold=self._threshold,
            allow_l1_bypass=self._l1_bypass,
        )
