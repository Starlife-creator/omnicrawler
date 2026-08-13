from __future__ import annotations

import importlib.util
import os
import platform
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from ..core.capabilities import capability_report
from ..core.config import AppConfig, validate_config

# P3-3 生态清单：doctor 预检校验「✅ 已融合」行落点模块真实存在（防文档漂移）
_ECOSYSTEM_DOC_NAME = "ECOSYSTEM_OBSERVATION.md"


def _module_from_rel_path(rel: str) -> str | None:
    """把生态清单里的相对模块路径（如 `core/site_aliases.py` / `convertx/`）转为点分模块名。"""
    rel = rel.strip().rstrip("/")
    if not rel or " " in rel:
        return None
    if rel.endswith((".yaml", ".yml", ".json", ".csv")):
        return None  # 数据/资源文件不参与模块校验
    if rel.endswith(".py"):
        rel = rel[:-3]
    parts = [part for part in rel.split("/") if part and part not in {".", "..", "**", "*"}]
    if not parts:
        return None
    if parts[0] != "omnicrawl":
        parts = ["omnicrawl", *parts]
    return ".".join(parts)


def check_ecosystem_doc() -> list[str]:
    """校验 docs/ECOSYSTEM_OBSERVATION.md 中「✅ 已融合」行的落点模块真实存在。

    仅产出 warning（文档陈旧不影响运行）；源码树外/文档缺失时静默跳过。
    """
    warnings: list[str] = []
    try:
        import omnicrawl

        docs_dir = Path(omnicrawl.__file__).resolve().parent.parent.parent / "docs"
    except Exception:  # noqa: BLE001
        return warnings
    doc = docs_dir / _ECOSYSTEM_DOC_NAME
    if not doc.is_file():
        return warnings
    try:
        for line in doc.read_text(encoding="utf-8").splitlines():
            if "✅ 已融合" not in line:
                continue
            for token in re.findall(r"`([^`]+)`", line):
                module = _module_from_rel_path(token)
                if module is None:
                    continue
                if importlib.util.find_spec(module) is None:
                    warnings.append(
                        f"生态清单已融合项落点缺失：`{token}`（{module} 不可导入）"
                    )
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"生态清单校验失败（不影响运行）：{exc}")
    return warnings


def _probe_models(
    base_url: str, api_key: str, model: str, *, config: AppConfig | None = None,
) -> dict[str, Any]:
    """轻量 GET {base_url}/models 探活；失败降级为 warning 不阻断（可能离线）。

    S2.5.22：传入 AppConfig 时改经 EgressBroker/安全 opener 探测（策略/审计/预算
    受约束，不探测私有目标），不再 urllib 直连。
    """
    import json
    import urllib.error
    import urllib.request
    from urllib.parse import urlparse

    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return {"ok": False, "detail": f"base_url 无效: {base_url!r}"}
    url = base_url.rstrip("/") + "/models"
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    if config is not None:
        from ..fetching.http_client import build_safe_opener
        from ..security.egress import EgressBroker

        opener = build_safe_opener(config, egress=EgressBroker(config))
    else:
        opener = urllib.request.build_opener()
    try:
        with opener.open(request, timeout=5) as response:
            body = json.loads(response.read(1024 * 1024).decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return {"ok": False, "detail": f"/models 探活返回 {exc.code}：API Key 无效或无权访问"}
        return {"ok": False, "detail": f"/models 探活返回 HTTP {exc.code}"}
    except Exception as exc:
        return {"ok": False, "detail": f"/models 探活失败（可能离线、地址不可达或策略拦截）: {exc}"}
    names = [
        item.get("id")
        for item in (body.get("data", []) if isinstance(body, dict) else [])
        if isinstance(item, dict) and item.get("id")
    ]
    found = model in names if names else None
    hint = "在返回列表中" if found else ("不在返回列表中（请核对模型名）" if found is False else "返回列表为空")
    return {"ok": True, "detail": f"/models 探活成功；配置模型 {model!r} {hint}", "models": len(names)}


def ai_health(
    project_root: str | Path | None = None,
    *,
    probe: bool = True,
    config: AppConfig | None = None,
) -> dict[str, Any]:
    """AI provider 环境体检：env 齐备性、OMNICRAWL_AI_* vs PDFX_LLM_* 一致性、可选 /models 探活。

    返回 ``status``：disabled / incomplete（启用但缺必填）/ configured / connected。
    """
    from ..core.ai_env import PDFX_ALIASES, load_ai_env

    env_vars = load_ai_env(project_root)
    provider = env_vars.get("OMNICRAWL_AI_PROVIDER", "disabled")
    base_url = env_vars.get("OMNICRAWL_AI_BASE_URL", "")
    model = env_vars.get("OMNICRAWL_AI_MODEL", "")
    api_key = env_vars.get("OMNICRAWL_AI_API_KEY", "")

    status = "disabled"
    issues: list[str] = []
    warnings: list[str] = []
    probe_result: dict[str, Any] | None = None

    if provider != "disabled":
        missing = [name for name, value in (("base_url", base_url), ("model", model)) if not value]
        if missing:
            status = "incomplete"
            issues.append(f"AI 已启用但缺少必填项: {', '.join(missing)}（请到 AI 服务中心补全）")
        else:
            status = "configured"
            if not api_key:
                warnings.append("AI 已启用但 API Key 为空；本地模型（如 Ollama）可忽略，云端模型会 401。")
            for target, source in PDFX_ALIASES.items():
                pdfx_value = env_vars.get(target)
                omni_value = env_vars.get(source)
                if pdfx_value and omni_value and pdfx_value != omni_value:
                    warnings.append(f"{target}={pdfx_value} 与 {source}={omni_value} 不一致，以 PDFX_LLM_* 为准")
            if probe:
                probe_result = _probe_models(base_url, api_key, model, config=config)
                if probe_result.get("ok"):
                    status = "connected"
                else:
                    warnings.append(probe_result["detail"])
    return {
        "status": status,
        "provider": provider,
        "base_url": base_url,
        "model": model,
        "api_key_set": bool(api_key),
        "issues": issues,
        "warnings": warnings,
        "probe": probe_result,
    }


def run_doctor(config: AppConfig, *, probe_ai: bool = True) -> dict[str, Any]:
    errors, warnings = validate_config(config)
    ai = ai_health(project_root=config.root, probe=probe_ai, config=config)
    errors.extend(ai["issues"])
    warnings.extend(ai["warnings"])
    capabilities = capability_report()
    dependencies = {
        name: importlib.util.find_spec(module) is not None
        for name, module in {
            "yaml": "yaml", "beautifulsoup4": "bs4", "openpyxl": "openpyxl",
            "pymupdf": "fitz", "playwright": "playwright", "selenium": "selenium",
            "httpx_async": "httpx", "websockets": "websockets", "redis": "redis", "scrapy": "scrapy",
            "paddleocr": "paddleocr", "pytesseract": "pytesseract",
        }.items()
    }
    usage = shutil.disk_usage(config.workspace.parent if config.workspace.parent.exists() else config.root)
    required: list[str] = ["yaml"]
    if config.source_kind == "browser":
        required.append(str(config.section("browser").get("engine", "playwright")))
    if config.source_kind == "websocket":
        required.append("websockets")
    if config.section("http").get("engine") == "httpx_async":
        required.append("httpx_async")
    if config.source_kind == "redis":
        required.append("redis")
    if config.source_kind == "scrapy":
        required.append("scrapy")
    if config.section("processors").get("pdf", {}).get("enabled"):
        required.extend(["pymupdf", "openpyxl"])
    missing = [name for name in dict.fromkeys(required) if not dependencies.get(name, False)]
    if missing:
        errors.append("缺少当前配置所需依赖: " + ", ".join(missing))
    # ── B-1：User-Agent 合规预检（反指纹对抗关键词扫描） ─────────
    user_agent_info: dict[str, Any] = {
        "profile": None,
        "configured_profile": None,
        "user_agent_set": False,
        "honest_self_report": True,
    }
    try:
        from ..core.utils import UA_PROFILES, build_user_agent

        http_section = config.section("http")
        cfg_ua_profile = str(http_section.get("user_agent_profile", "")).strip() or None
        cfg_ua = str(http_section.get("user_agent", "")).strip() or None
        user_agent_info["configured_profile"] = cfg_ua_profile
        user_agent_info["user_agent_set"] = cfg_ua is not None
        final_profile = cfg_ua_profile or "polite_bot"
        user_agent_info["profile"] = final_profile
        # ① profile 名反指纹关键词扫描
        _ANTI_FP_KEYWORDS = (
            "random", "fake", "spoof", "forge", "anti-fingerprint",
            "antifingerprint", "canvas", "webgl", "audio-fingerprint",
            "font-fingerprint", "webdriver-false", "navigator-spoof",
        )
        haystack_parts: list[str] = [cfg_ua_profile or "", cfg_ua or ""]
        # 如果 cfg_ua_profile 不在 UA_PROFILES（用户自定义非官方 profile），加提醒
        if cfg_ua_profile and cfg_ua_profile.lower() not in {k.lower() for k in UA_PROFILES}:
            warnings.append(
                f"http.user_agent_profile={cfg_ua_profile!r} 不在官方合规 UA_PROFILES "
                f"（{sorted(UA_PROFILES)}），建议使用官方 profile 或自行通过 doctor 复核合规性。"
            )
        detected: list[str] = []
        for kw in _ANTI_FP_KEYWORDS:
            for part in haystack_parts:
                if kw.lower() in part.lower():
                    detected.append(kw)
                    break
        if detected:
            warnings.append(
                "http.user_agent/user_agent_profile 检测到反指纹对抗疑似关键词："
                + ", ".join(sorted(detected))
                + " → 该方向属于 RESEARCH_AND_FUSION.md 明确禁止吸收的对抗行为，"
                + "请在运行前确认已删除。"
            )
            user_agent_info["honest_self_report"] = False
        # ② 如果 cfg_ua 被手动覆盖，用铁则 validate_profile_honest 复核一次
        if cfg_ua:
            try:
                from ..core.utils import _validate_profile_honest

                _validate_profile_honest(cfg_ua, profile_name="http.user_agent (manual)")
            except ValueError as exc:
                warnings.append(f"http.user_agent 不符合合规铁则：{exc}")
                user_agent_info["honest_self_report"] = False
        else:
            # 构建一遍默认 UA 过铁则（保证默认 profile 也合规）
            try:
                build_user_agent(final_profile, suffix="+doctor-check")
            except ValueError as exc:
                warnings.append(f"User-Agent profile 构建失败：{exc}")
                user_agent_info["honest_self_report"] = False
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"User-Agent 合规预检异常（不影响运行）：{exc}")
    # ── B-3：Mirror 预检（静态安全校验） ──────────────────
    mirror_info: dict[str, Any] = {"enabled": False, "groups": 0, "all_ok": True}
    try:
        from ..sources.mirror_registry import MirrorConfigError, MirrorRegistry
        try:
            mr = MirrorRegistry(config)
            mirror_info = mr.validation_snapshot()
            if mr.enabled and not mirror_info["all_ok"]:
                errors.append(
                    "mirrors.groups 存在不安全配置，请检查："
                    + "；".join(
                        f"{canonical}: " + ", ".join(
                            r["host"] for r in rows if not r["ok"]
                        )
                        for canonical, rows in mirror_info["groups"].items()
                        if any(not r["ok"] for r in rows)
                    )
                )
        except MirrorConfigError as exc:
            errors.append(f"mirrors.groups 预检失败（fail-fast）：{exc}")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"MirrorRegistry 预检异常（不影响运行，已跳过镜像路由）：{exc}")
    except Exception:  # noqa: BLE001
        pass  # 模块不可用时不报错
    # ── B-2：Site Categorizer 预检（YAML 语法红牌 / 模板不存在黄牌 / L3 围栏） ──
    categorizer_info: dict[str, Any] = {
        "loaded": False,
        "loaded_sources": [],
        "mapping_count": 0,
        "fallback_mapping_count": 0,
        "l3_enabled": False,
        "l3_implemented": False,
        "l3_requires_fetcher": True,
        "l3_request_shape": "HEAD + Range: bytes=0-8192",
        "l3_default_timeout_s": 2.0,
        "missing_templates": [],
        "last_error": None,
    }
    try:
        from ..core.categorizer import (
            _L3_SNIFF_CONFIDENCE,  # noqa: F401  仅用于证明 Phase2 L3 实现已加载
            _L3_TIMEOUT_S_DEFAULT,
            SiteCategorizer,
        )
        from ..templates.template_catalog import TemplateCatalog

        categorizer_info["l3_implemented"] = True
        categorizer_info["l3_default_timeout_s"] = float(_L3_TIMEOUT_S_DEFAULT)

        try:
            sc = SiteCategorizer.from_app_config(config, project_root=config.root)
            categorizer_info["loaded"] = sc.last_error() is None
            categorizer_info["loaded_sources"] = list(sc.loaded_sources())
            categorizer_info["mapping_count"] = len(sc.mappings)
            categorizer_info["fallback_mapping_count"] = len(sc.fallback_mapping)
            categorizer_info["l3_enabled"] = sc.enable_sniffing
            # L3 围栏：启用时提醒需由上层传绑定 EgressBroker 审计通道的 fetcher 实例（黄牌，不阻断）
            if sc.enable_sniffing:
                warnings.append(
                    "B-2：source.categorizer.enable_sniffing=true。"
                    "L3 嗅探已实现（HEAD+Range 0-8192, 2s 串行, 不跟随 3xx），"
                    "但需由 wizard/GUI 把已绑定 EgressBroker 安全审计通道的 AsyncFetcher 实例传入 "
                    "SiteCategorizer.classify(fetcher=...)，否则会回退 generic_html 兜底并提示 no-fetcher。"
                )
            # YAML 加载错误 → 红牌（fail-fast：可能是用户手改 YAML 语法错）
            if sc.last_error() is not None:
                errors.append(
                    "B-2：Site Categorizer YAML 加载失败：" + sc.last_error()
                    + "（已保留上一份有效配置；若无，则 L2 映射为空，仅走 L1 + generic 兜底）"
                )
            categorizer_info["last_error"] = sc.last_error()
            # 模板存在性校验：加载 catalog 后扫一遍 L2 mappings + fallback_mapping 的 values
            try:
                catalog = TemplateCatalog.from_app_config(config)
                missing: list[str] = []
                all_template_ids = set(sc.mappings.values()) | set(sc.fallback_mapping.values())
                for tid in sorted(all_template_ids):
                    if catalog.get(tid) is None:
                        missing.append(tid)
                categorizer_info["missing_templates"] = missing
                if missing:
                    # 黄牌：模板不存在时会走 fallback_mapping → 最终 generic，不崩溃但可能抓不准
                    warnings.append(
                        "B-2：以下模板 ID 在 catalog 中不存在（将按 fallback_mapping → "
                        "generic_html 兜底，可能降低抓取质量）："
                        + ", ".join(f"`{m}`" for m in missing)
                    )
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"B-2：TemplateCatalog 加载失败，跳过模板存在性校验：{exc}")
            # 命中率 info 级：用空列表 classify 一下触发计数器初始化（不做真实 URL，避免预检耗时长）
        except Exception as exc:  # noqa: BLE001
            errors.append(f"B-2：Site Categorizer 初始化异常（fail-fast）：{exc}")
            categorizer_info["last_error"] = str(exc)
    except Exception:  # noqa: BLE001
        pass  # 模块不可用时静默跳过（不影响旧配置运行）
    # ── P3-3：生态清单落点校验（防文档漂移，仅 warning） ──
    warnings.extend(check_ecosystem_doc())
    return {
        "ok": not errors,
        "python": sys.version.split()[0], "platform": platform.platform(),
        "config": str(config.path), "workspace": str(config.workspace),
        "workspace_writable": os.access(config.workspace.parent if config.workspace.parent.exists() else config.root, os.W_OK),
        "disk_free_gb": round(usage.free / 1024 ** 3, 2),
        "dependencies": dependencies,
        "capabilities": capabilities,
        "native_runtime": capabilities["native"],
        "ai": ai,
        "user_agent": user_agent_info,
        "mirror": mirror_info,
        "categorizer": categorizer_info,
        "errors": errors,
        "warnings": warnings,
    }
