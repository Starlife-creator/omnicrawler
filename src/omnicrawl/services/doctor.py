from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import sys
from typing import Any

from ..core.capabilities import capability_report
from ..core.config import AppConfig, validate_config


def run_doctor(config: AppConfig) -> dict[str, Any]:
    errors, warnings = validate_config(config)
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
        "errors": errors,
        "warnings": warnings,
    }
