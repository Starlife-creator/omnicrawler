"""Application-wide runtime capability discovery.

The report distinguishes Python packages from native/data components so a
successful installation can never conceal a missing browser, OCR engine or
offline model cache.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from .. import __version__

MODULES = {
    "core_yaml": "yaml",
    "html": "bs4",
    "xml": "lxml",
    "pdf": "fitz",
    "xlsx": "openpyxl",
    "playwright": "playwright",
    "selenium": "selenium",
    "async_http": "httpx",
    "tls_impersonate": "curl_cffi",
    "websocket": "websockets",
    "redis": "redis",
    "scrapy": "scrapy",
    "tesseract_wrapper": "pytesseract",
    "paddle": "paddle",
    "paddleocr": "paddleocr",
    "gui": "PyQt6",
    "keyring": "keyring",
    "s3": "boto3",
    "duckdb": "duckdb",
    "parquet": "pyarrow",
    "postgresql": "psycopg",
    "opensearch": "opensearchpy",
}

# A task check verifies only the components needed by the user's next action.
# Keep feature names stable because they are exposed by the CLI and diagnostics.
FEATURE_REQUIREMENTS: dict[str, dict[str, tuple[str, ...]]] = {
    "core": {"modules": ("core_yaml",), "native": ()},
    "web": {"modules": ("html", "xml"), "native": ()},
    "pdf": {"modules": ("pdf", "xlsx"), "native": ()},
    "browser": {"modules": ("playwright",), "native": ("chromium",)},
    "selenium": {"modules": ("selenium",), "native": ("selenium_driver",)},
    "ocr-tesseract": {"modules": ("tesseract_wrapper",), "native": ("tesseract",)},
    "ocr-paddle": {"modules": ("paddle", "paddleocr"), "native": ("paddle_models",)},
    "gui": {"modules": ("gui",), "native": ()},
    "streams": {"modules": ("websocket",), "native": ()},
    "storage-s3": {"modules": ("s3",), "native": ()},
    "storage-duckdb": {"modules": ("duckdb",), "native": ()},
    "storage-parquet": {"modules": ("parquet",), "native": ()},
    "storage-postgresql": {"modules": ("postgresql",), "native": ()},
    "storage-opensearch": {"modules": ("opensearch",), "native": ()},
}


def _path_from_env(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser() if value else None


def _existing_file(*candidates: Path | None) -> str | None:
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return str(candidate.resolve())
    return None


def _playwright_browser() -> str | None:
    root = _path_from_env("PLAYWRIGHT_BROWSERS_PATH")
    if root is None or not root.is_dir():
        return None
    for pattern in ("chromium-*/chrome-win/chrome.exe", "chromium-*/chrome-win64/chrome.exe", "chromium-*/chrome-linux/chrome", "chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium"):
        match = next(root.glob(pattern), None)
        if match is not None:
            return str(match.resolve())
    return None


def capability_report(
    verify_imports: bool = False,
    *,
    portable_paths: bool = False,
    mode: str = "quick",
    require_features: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Return a layered capability report without needlessly importing heavy modules.

    ``quick`` checks installation metadata (defaulting to the core runtime;
    explicit ``require_features`` are honored since S2.1.1), ``task`` checks
    only the features explicitly requested, and ``deep`` imports every
    installed optional module.  ``verify_imports=True`` remains available for
    backwards compatibility and performs the old all-module import check.
    """

    mode = mode.casefold()
    if mode not in {"quick", "task", "deep"}:
        raise ValueError("能力检查模式只能是 quick、task 或 deep")
    requested = tuple(dict.fromkeys(require_features or ("core",)))
    unknown = sorted(set(requested) - set(FEATURE_REQUIREMENTS))
    if unknown:
        raise ValueError(f"未知能力名称: {', '.join(unknown)}")
    # S2.1.1 后项（源B P1#30）：quick 模式不再静默丢弃显式 require_features；
    # 未显式提供时仍默认只检查核心运行环境

    required_modules = {
        module for feature in requested for module in FEATURE_REQUIREMENTS[feature]["modules"]
    }
    import_modules = set(MODULES) if verify_imports or mode == "deep" else required_modules
    modules: dict[str, dict[str, Any]] = {}
    for feature, module in MODULES.items():
        installed = importlib.util.find_spec(module) is not None
        record: dict[str, Any] = {"module": module, "installed": installed}
        if installed and feature in import_modules:
            try:
                imported = importlib.import_module(module)
                record["importable"] = True
                record["version"] = str(getattr(imported, "__version__", "unknown"))
            except Exception as exc:  # noqa: BLE001 - diagnostics must retain all failures
                record["importable"] = False
                record["error"] = f"{type(exc).__name__}: {exc}"
        modules[feature] = record

    tesseract = _existing_file(_path_from_env("TESSERACT_CMD"), Path(shutil.which("tesseract") or ""))
    driver = _existing_file(_path_from_env("OMNICRAWL_SELENIUM_DRIVER"), Path(shutil.which("chromedriver") or ""))
    chrome = _existing_file(_path_from_env("OMNICRAWL_CHROME_BINARY"), Path(_playwright_browser() or ""))
    paddle_cache = _path_from_env("PADDLE_PDX_CACHE_HOME")
    models: list[str] = []
    if paddle_cache is not None and paddle_cache.is_dir():
        official = paddle_cache / "official_models"
        if official.is_dir():
            # F41：仅当模型权重完整（inference.pdiparams）才算就绪，目录存在不代表可用
            models = sorted(
                path.name for path in official.iterdir()
                if path.is_dir() and (path / "inference.pdiparams").is_file()
            )
    tessdata_dir = _path_from_env("TESSDATA_PREFIX")
    tess_langs: list[str] = []
    if tessdata_dir is not None and tessdata_dir.is_dir():
        tess_langs = sorted(path.stem for path in tessdata_dir.glob("*.traineddata"))
    required_tess = {"eng", "chi_sim"}
    # F39：Tesseract 就绪还需所需语言包存在，且报告实际可用语言
    tess_ready = tesseract is not None and required_tess.issubset(set(tess_langs))
    native: dict[str, dict[str, Any]] = {
        "chromium": {"ready": chrome is not None, "path": chrome},
        "selenium_driver": {"ready": driver is not None, "path": driver},
        "tesseract": {
            "ready": tess_ready, "path": tesseract,
            "languages": tess_langs,
            "missing_languages": sorted(required_tess - set(tess_langs)),
        },
        "paddle_models": {"ready": bool(models), "path": str(paddle_cache) if paddle_cache else None, "models": models},
    }
    if portable_paths:
        application_root = Path(sys.executable).resolve().parent
        for record in native.values():
            value = record.get("path")
            if not value:
                continue
            try:
                relative = Path(str(value)).resolve().relative_to(application_root)
                record["path"] = "${APP_DIR}/" + relative.as_posix()
            except ValueError:
                record["path"] = "${EXTERNAL}/" + Path(str(value)).name

    # 分级能力报告：Standard 核心能力、Full 专属组件、可选集成
    standard_capabilities = _build_standard_capabilities(modules, native)
    full_capabilities = _build_full_capabilities(modules, native)
    optional_capabilities = _build_optional_capabilities(modules)

    core_ready = modules["core_yaml"]["installed"] and modules["core_yaml"].get("importable", True)
    installed_modules_importable = all(
        not item["installed"] or item.get("importable", True) for item in modules.values()
    )
    feature_checks: dict[str, dict[str, Any]] = {}
    for feature in requested:
        requirement = FEATURE_REQUIREMENTS[feature]
        missing_modules = [
            item for item in requirement["modules"]
            if not modules[item]["installed"] or modules[item].get("importable") is False
        ]
        missing_native = [item for item in requirement["native"] if not native[item]["ready"]]
        feature_checks[feature] = {
            "ready": not missing_modules and not missing_native,
            "missing_modules": missing_modules,
            "missing_native": missing_native,
        }
    requirements_ready = all(item["ready"] for item in feature_checks.values())
    strict_imports = verify_imports or mode == "deep"
    return {
        "ok": core_ready and requirements_ready and (installed_modules_importable if strict_imports else True),
        "all_optional_ready": all(
            item["installed"] and item.get("importable", True) for item in modules.values()
        ),
        "version": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "modules": modules,
        "native": native,
        "standard": standard_capabilities,
        "full": full_capabilities,
        "optional": optional_capabilities,
        "check": {
            "mode": mode,
            "requested_features": list(requested),
            "features": feature_checks,
            "imported_modules": sorted(import_modules),
            "unverified_modules": sorted(set(MODULES) - import_modules),
        },
    }


def _build_standard_capabilities(modules: dict[str, dict[str, Any]], native: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Standard 便携包核心能力分级报告。"""
    items = {}
    # 核心解析
    items["core_yaml"] = _cap_item("YAML 配置解析", modules.get("core_yaml", {}).get("installed", False),
                                   "配置文件读写", "standard")
    items["html_parsing"] = _cap_item("HTML 解析 (BeautifulSoup)", modules.get("html", {}).get("installed", False),
                                      "网页内容提取", "standard")
    items["xml_parsing"] = _cap_item("XML/Feed 解析 (lxml)", modules.get("xml", {}).get("installed", False),
                                     "RSS/Atom/Sitemap 处理", "standard")
    items["async_http"] = _cap_item("异步 HTTP (httpx)", modules.get("async_http", {}).get("installed", False),
                                    "高性能网页和 API 请求", "standard")
    items["tls_impersonate"] = _cap_item("TLS 指纹伪装 (curl_cffi)", modules.get("tls_impersonate", {}).get("installed", False),
                                         "模拟浏览器 TLS 握手对抗检测", "standard")
    items["pdf_text"] = _cap_item("PDF 文本解析 (PyMuPDF)", modules.get("pdf", {}).get("installed", False),
                                  "提取 PDF 文字、表格和元数据", "standard")
    items["xlsx"] = _cap_item("Excel 导出 (openpyxl)", modules.get("xlsx", {}).get("installed", False),
                              "生成 Excel 格式结果", "standard")
    items["playwright"] = _cap_item("Playwright 浏览器自动化", modules.get("playwright", {}).get("installed", False),
                                    "动态页面渲染和交互", "standard")
    items["chromium"] = _cap_item("Chromium 浏览器引擎", native.get("chromium", {}).get("ready", False),
                                  "Playwright 的浏览器运行时", "standard")
    items["gui"] = _cap_item("桌面界面 (PyQt6)", modules.get("gui", {}).get("installed", False),
                             "图形化工作台", "standard")
    items["websocket"] = _cap_item("WebSocket 支持", modules.get("websocket", {}).get("installed", False),
                                   "实时数据流采集", "standard")

    all_ready = all(item["ready"] for item in items.values())
    return {
        "title": "Standard 核心能力",
        "description": "适合普通网页、API、文件、PDF 和主流桌面任务",
        "all_ready": all_ready,
        "items": items,
        "summary": "全部就绪" if all_ready else
                   "部分组件缺失——运行前请检查环境",
    }


def _build_full_capabilities(modules: dict[str, dict[str, Any]], native: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Full 便携包专属组件分级报告。"""
    items = {}
    items["selenium"] = _cap_item("Selenium 浏览器驱动", modules.get("selenium", {}).get("installed", False),
                                  "传统浏览器自动化和兼容网站", "full")
    items["selenium_driver"] = _cap_item("ChromeDriver (Selenium)", native.get("selenium_driver", {}).get("ready", False),
                                         "Selenium 的 Chrome 驱动", "full")
    items["tesseract_ocr"] = _cap_item("Tesseract OCR 引擎", native.get("tesseract", {}).get("ready", False),
                                       "扫描件和图片文字识别", "full")
    items["paddleocr"] = _cap_item("PaddleOCR 结构化识别", modules.get("paddleocr", {}).get("installed", False),
                                   "表格、版面分析和中文 OCR", "full")
    items["paddle_models"] = _cap_item("PaddleOCR 离线模型", native.get("paddle_models", {}).get("ready", False),
                                       "版面/表格/文字检测模型", "full")
    # S4.5 P3#150：keyring 文案按平台显示，不再全平台写 "Windows 凭据管理器"
    if sys.platform == "win32":
        keyring_label = "Windows 凭据管理器 (keyring)"
    elif sys.platform == "darwin":
        keyring_label = "macOS Keychain (keyring)"
    else:
        keyring_label = "Secret Service / GNOME Keyring (keyring)"
    items["keyring"] = _cap_item(keyring_label, modules.get("keyring", {}).get("installed", False),
                                 "安全存储 API Key 和密码", "full")

    ready_count = sum(1 for item in items.values() if item["ready"])
    return {
        "title": "Full 专属组件",
        "description": "需要完整离线 OCR、Selenium 和本机模型时使用 Full 便携包",
        "ready_count": ready_count,
        "total": len(items),
        "items": items,
        "summary": f"{ready_count}/{len(items)} 就绪"
                   if ready_count == len(items) else
                   f"{ready_count}/{len(items)} 就绪——缺少的组件可通过安装 Full 包或手动配置获取",
    }


def _build_optional_capabilities(modules: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """可选的专业存储与集成组件。"""
    items = {}
    items["redis"] = _cap_item("Redis 分布式队列", modules.get("redis", {}).get("installed", False),
                               "多 Worker 分布式采集", "optional")
    items["scrapy"] = _cap_item("Scrapy 集成", modules.get("scrapy", {}).get("installed", False),
                                "复用 Scrapy 蜘蛛和中间件", "optional")
    items["s3"] = _cap_item("S3 对象存储 (boto3)", modules.get("s3", {}).get("installed", False),
                            "结果和附件上传到云存储", "optional")
    items["duckdb"] = _cap_item("DuckDB 分析数据库", modules.get("duckdb", {}).get("installed", False),
                                "大规模结果查询和分析", "optional")
    items["parquet"] = _cap_item("Parquet 列式存储", modules.get("parquet", {}).get("installed", False),
                                 "高效压缩的大数据格式", "optional")
    items["postgresql"] = _cap_item("PostgreSQL 存储", modules.get("postgresql", {}).get("installed", False),
                                    "关系数据库后端", "optional")
    items["opensearch"] = _cap_item("OpenSearch 全文检索", modules.get("opensearch", {}).get("installed", False),
                                    "全文搜索和分析", "optional")

    installed = sum(1 for item in items.values() if item["ready"])
    return {
        "title": "可选专业集成",
        "description": "专业存储客户端和分布式组件——按需安装",
        "installed": installed,
        "total": len(items),
        "items": items,
        "summary": f"已安装 {installed}/{len(items)} 项可选组件"
                   if installed else "未安装可选组件——当前任务不需要",
    }


def _cap_item(name: str, ready: bool, description: str, tier: str) -> dict[str, Any]:
    return {"name": name, "ready": ready, "description": description, "tier": tier}


def capability_summary_text(report: dict[str, Any]) -> str:
    """生成面向用户的分级能力摘要文本。"""
    lines = [f"OmniCrawler {report['version']} — Python {report['python']} — {report['platform']}", ""]

    check = report.get("check", {})
    if check:
        features = ", ".join(check.get("requested_features", []))
        lines.append(f"【本次检查】{check.get('mode', 'quick')} — {features}")
        for feature, result in check.get("features", {}).items():
            icon = "✓" if result.get("ready") else "✗"
            missing = [*result.get("missing_modules", []), *result.get("missing_native", [])]
            suffix = "" if not missing else f"（缺少：{', '.join(missing)}）"
            lines.append(f"  {icon} {feature}{suffix}")
        lines.append("")

    standard = report.get("standard", {})
    lines.append(f"【{standard.get('title', 'Standard 核心')}】{standard.get('summary', '')}")
    if standard.get("items"):
        for item in standard["items"].values():
            icon = "✓" if item["ready"] else "✗"
            lines.append(f"  {icon} {item['name']}：{item['description']}")
    lines.append("")

    full = report.get("full", {})
    lines.append(f"【{full.get('title', 'Full 专属')}】{full.get('summary', '')}")
    if full.get("items"):
        for item in full["items"].values():
            icon = "✓" if item["ready"] else "○"
            status = "" if item["ready"] else "（需要 Full 包或安装组件）"
            lines.append(f"  {icon} {item['name']}：{item['description']}{status}")
    lines.append("")

    optional = report.get("optional", {})
    lines.append(f"【{optional.get('title', '可选集成')}】{optional.get('summary', '')}")
    if optional.get("items"):
        for item in optional["items"].values():
            icon = "✓" if item["ready"] else "·"
            status = "，当前任务不需要" if not item["ready"] else ""
            lines.append(f"  {icon} {item['name']}：{item['description']}{status}")

    return "\n".join(lines)


def runtime_self_test() -> dict[str, Any]:
    """Run offline OCR engines with generated input; never contacts a service."""
    tests: dict[str, dict[str, Any]] = {}
    try:
        import pytesseract
        from PIL import Image, ImageDraw

        command = os.environ.get("TESSERACT_CMD", "").strip()
        if command:
            pytesseract.pytesseract.tesseract_cmd = command
        image = Image.new("RGB", (520, 100), "white")
        ImageDraw.Draw(image).text((20, 30), "OmniCrawler OCR 1.0", fill="black")
        text = pytesseract.image_to_string(image, lang="eng").strip()
        # F40：同时对 chi_sim 做实际识别，语言包缺失/损坏立即暴露，不再只验 eng
        chi_result = False
        chi_error = ""
        try:
            zh_image = Image.new("RGB", (560, 100), "white")
            # F40：PIL 默认位图字体无中文字形，用 ASCII 文本保证 chi_sim 可识别
            ImageDraw.Draw(zh_image).text((20, 30), "OCR 123", fill="black")
            chi_result = bool(pytesseract.image_to_string(zh_image, lang="chi_sim").strip())
        except Exception as exc:  # noqa: BLE001
            chi_error = f"{type(exc).__name__}: {exc}"
        tests["tesseract_ocr"] = {
            "ok": bool(text) and chi_result, "text": text[:120],
            "chi_sim": chi_result, "chi_sim_error": chi_error,
        }
    except Exception as exc:  # noqa: BLE001
        tests["tesseract_ocr"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    try:
        import numpy as np
        from paddleocr import PPStructureV3
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (720, 120), "white")
        ImageDraw.Draw(image).text((20, 30), "OmniCrawler PaddleOCR 1.0", fill="black")
        pipeline = PPStructureV3(
            lang="ch",
            device="cpu",
            use_doc_orientation_classify=True,
            use_doc_unwarping=False,
            use_textline_orientation=True,
            use_table_recognition=True,
            use_formula_recognition=False,
            use_chart_recognition=False,
        )
        results = list(pipeline.predict(np.asarray(image)))
        tests["paddle_structure"] = {"ok": bool(results), "results": len(results)}
    except Exception as exc:  # noqa: BLE001
        tests["paddle_structure"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    return {"ok": all(item["ok"] for item in tests.values()), "tests": tests}
