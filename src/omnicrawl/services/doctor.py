from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from ..core.capabilities import capability_report
from ..core.config import AppConfig, validate_config


def _probe_models(base_url: str, api_key: str, model: str) -> dict[str, Any]:
    """轻量 GET {base_url}/models 探活；失败降级为 warning 不阻断（可能离线）。"""
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
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            body = json.loads(response.read(1024 * 1024).decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return {"ok": False, "detail": f"/models 探活返回 {exc.code}：API Key 无效或无权访问"}
        return {"ok": False, "detail": f"/models 探活返回 HTTP {exc.code}"}
    except Exception as exc:
        return {"ok": False, "detail": f"/models 探活失败（可能离线或地址不可达）: {exc}"}
    names = [
        item.get("id")
        for item in (body.get("data", []) if isinstance(body, dict) else [])
        if isinstance(item, dict) and item.get("id")
    ]
    found = model in names if names else None
    hint = "在返回列表中" if found else ("不在返回列表中（请核对模型名）" if found is False else "返回列表为空")
    return {"ok": True, "detail": f"/models 探活成功；配置模型 {model!r} {hint}", "models": len(names)}


def ai_health(project_root: str | Path | None = None, *, probe: bool = True) -> dict[str, Any]:
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
                probe_result = _probe_models(base_url, api_key, model)
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
    ai = ai_health(project_root=config.root, probe=probe_ai)
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
        "errors": errors,
        "warnings": warnings,
    }
