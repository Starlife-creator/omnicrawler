"""Runtime path helpers shared by source and frozen GUI builds."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)


def is_frozen() -> bool:
    """Return whether the application is running from a freezer bundle."""
    return bool(getattr(sys, "frozen", False))


def application_dir() -> Path:
    """Return the user-visible application directory."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


def bundle_root() -> Path:
    """Return the root used for package resources bundled by PyInstaller."""
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root)
    return Path(__file__).resolve().parents[2]


def package_resource(*parts: str) -> Path:
    """Resolve a resource stored under the source/package root."""
    return bundle_root().joinpath(*parts)


@lru_cache(maxsize=1)
def portable_data_root() -> Path:
    """Choose an app-local writable workspace, with a safe OS fallback."""
    preferred = application_dir()
    choice = _data_mode_choice(preferred)
    if choice.get("mode") == "custom" and choice.get("root"):
        selected = Path(str(choice["root"])).expanduser()
        selected = selected if selected.is_absolute() else preferred / selected
        selected.mkdir(parents=True, exist_ok=True)
        return selected.resolve()
    marker = (preferred / "PORTABLE.flag").is_file() or (preferred / "portable.flag").is_file()
    if choice.get("mode") == "portable" or marker or (is_frozen() and choice.get("mode") != "local"):
        try:
            probe = preferred / ".omnicrawler"
            probe.mkdir(parents=True, exist_ok=True)
            return preferred
        except OSError:
            pass

    local_app_data = os.environ.get("LOCALAPPDATA")
    fallback = Path(local_app_data) / "OmniCrawler" if local_app_data else Path.home() / ".omnicrawler"
    try:
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback
    except OSError:
        # Last-resort path for locked-down corporate profiles. The app remains
        # usable for the current session even when normal profile storage is denied.
        emergency = Path(os.environ.get("TEMP", str(application_dir()))) / "OmniCrawler"
        emergency.mkdir(parents=True, exist_ok=True)
        # F29：兜底数据根落在临时目录，必须显式告知（会被系统清理、无声丢数据）
        logger.warning(
            "无法创建常规数据目录，工作区落入临时目录: %s；重启后数据可能被系统清理，请改用 portable/local 数据模式",
            emergency,
        )
        try:
            (emergency / "DATA-ROOT-NOTICE.txt").write_text(
                "此目录是 OmniCrawler 的临时兜底数据根。\n"
                "常规数据目录（%LOCALAPPDATA% 或应用目录）不可写，本目录可能被系统清理。\n"
                "请在设置中改用 portable 或 custom 数据模式以保留配置与结果。\n",
                encoding="utf-8",
            )
        except OSError:
            pass
        return emergency


def configure_data_mode(mode: str, custom_root: str = "") -> Path:
    """Persist first-launch portable/local/custom selection without environment variables."""

    if mode not in {"portable", "local", "custom"}:
        raise ValueError("数据模式必须是portable、local或custom")
    if mode == "custom" and not custom_root.strip():
        raise ValueError("custom数据模式必须选择目录")
    path = application_dir() / "data-mode.json"
    path.write_text(json.dumps({"mode": mode, "root": custom_root}, ensure_ascii=False, indent=2), encoding="utf-8")
    portable_data_root.cache_clear()
    return portable_data_root()


def resolve_portable_path(value: str) -> Path:
    # B05-018：只接受基于 ${APP_DIR}/${DATA_DIR} 的配置路径——展开后必须仍位于
    # 应用目录或数据根内，拒绝任意绝对路径 / ../ 逃逸（防越界读写）。
    expanded = value.replace("${APP_DIR}", str(application_dir())).replace("${DATA_DIR}", str(portable_data_root()))
    resolved = Path(expanded).expanduser().resolve()
    roots = (application_dir(), portable_data_root())
    if not any(resolved == root or root in resolved.parents for root in roots):
        raise ValueError(f"路径越出应用/数据根目录: {resolved}")
    return resolved


def storage_advisory(path: Path) -> dict[str, object]:
    """Describe removable/network-drive risks without writing benchmark files."""

    resolved = path.expanduser().resolve()
    drive_type = "unknown"
    warnings: list[str] = []
    if sys.platform == "win32":
        try:
            import ctypes

            value = ctypes.windll.kernel32.GetDriveTypeW(resolved.drive + "\\")
            drive_type = {2: "removable", 3: "fixed", 4: "network", 5: "optical", 6: "ramdisk"}.get(value, "unknown")
        except (AttributeError, OSError):
            pass
    if drive_type in {"removable", "network"}:
        warnings.append("该工作区位于移动盘或网络盘，数据库与OCR可能明显变慢。")
        warnings.append("任务运行或写入尚未完成时不要弹出、断开或拔出设备。")
    return {"path": str(resolved), "drive_type": drive_type, "warnings": warnings}


def _data_mode_choice(root: Path) -> dict[str, object]:
    for name in ("data-mode.json", "DATA_MODE.json"):
        try:
            value = json.loads((root / name).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            return value
    return {}


def browsers_root() -> Path:
    """Bundled browsers 目录单一真源（F27：修复两处路径不一致）。"""
    if is_frozen():
        return application_dir() / "browsers"
    app_dir = application_dir()
    for candidate in (app_dir / ".runtime" / "browsers", app_dir / "runtime" / "browsers"):
        if candidate.is_dir():
            return candidate
    return app_dir / ".runtime" / "browsers"


def configure_runtime_environment() -> None:
    """Configure paths needed by optional bundled runtime components."""
    app_dir = application_dir()
    runtime_dir = app_dir / "runtime"
    if not is_frozen() and (app_dir / ".runtime").is_dir():
        runtime_dir = app_dir / ".runtime"
    if not is_frozen() and not runtime_dir.is_dir():
        return
    cache_dir = portable_data_root() / ".omnicrawler" / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    browsers = browsers_root()
    if browsers.is_dir():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(browsers))
    paddle_models = runtime_dir / "models" / "paddlex"
    os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(paddle_models if paddle_models.is_dir() else cache_dir / "paddlex"))
    os.environ.setdefault("PADDLE_HOME", str(cache_dir / "paddle"))
    os.environ.setdefault("XDG_CACHE_HOME", str(cache_dir))
    os.environ.setdefault("HF_HOME", str(cache_dir / "huggingface"))
    os.environ.setdefault("MODELSCOPE_CACHE", str(cache_dir / "modelscope"))
    if sys.platform == "win32":
        # Paddle 3.3's oneDNN/PIR path cannot execute every PPStructureV3
        # detector on Windows; the regular CPU runner is complete and stable.
        os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "False")
    # F28：冻结模式下逐资产记录状态（缺失时 warning + runtime-status.json 供 GUI 展示）
    runtime_status: dict[str, str] = {}
    tesseract = runtime_dir / "tesseract" / "tesseract.exe"
    tessdata = runtime_dir / "tesseract" / "tessdata"
    if tesseract.is_file():
        os.environ.setdefault("TESSERACT_CMD", str(tesseract))
        os.environ.setdefault("TESSDATA_PREFIX", str(tessdata))
        runtime_status["tesseract"] = "ok"
    elif is_frozen():
        logger.warning("内置 Tesseract 缺失: %s（中文 OCR 不可用，请重解压完整包）", tesseract)
        runtime_status["tesseract"] = "missing"
    driver = runtime_dir / "selenium" / "chromedriver.exe"
    if driver.is_file():
        os.environ.setdefault("OMNICRAWL_SELENIUM_DRIVER", str(driver))
        runtime_status["chromedriver"] = "ok"
    elif is_frozen():
        logger.warning("内置 ChromeDriver 缺失: %s（浏览器自动化不可用）", driver)
        runtime_status["chromedriver"] = "missing"
    chrome = bundled_browser_executable()
    if chrome is not None:
        os.environ.setdefault("OMNICRAWL_CHROME_BINARY", str(chrome))
        runtime_status["chromium"] = "ok"
    elif is_frozen():
        logger.warning("内置 Chromium 缺失（浏览器采集不可用）")
        runtime_status["chromium"] = "missing"
    if is_frozen():
        os.environ.setdefault("OMNICRAWL_PORTABLE_ROOT", str(portable_data_root()))
        try:
            status_path = portable_data_root() / ".omnicrawler" / "runtime-status.json"
            status_path.parent.mkdir(parents=True, exist_ok=True)
            status_path.write_text(json.dumps(runtime_status, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass


def bundled_browser_available() -> bool:
    """Return whether a usable Playwright Chromium is shipped with the app."""
    return bundled_browser_executable() is not None


def bundled_browser_executable() -> Path | None:
    """Return the bundled Chromium executable, if present."""
    browsers = browsers_root()
    for pattern in ("chromium-*/chrome-win/chrome.exe", "chromium-*/chrome-win64/chrome.exe"):
        match = next(browsers.glob(pattern), None) if browsers.is_dir() else None
        if match is not None:
            return match
    return None


def bundled_cli_path() -> Path | None:
    """Locate the CLI shipped next to a frozen GUI executable."""
    if not is_frozen():
        return None
    suffix = ".exe" if sys.platform == "win32" else ""
    for name in (f"omnicrawl{suffix}", f"OmniCrawler-cli{suffix}"):
        candidate = application_dir() / name
        if candidate.is_file():
            return candidate
    return None


# B05-017：configured CLI 白名单——只接受项目 CLI 名或位于应用/项目根内的可执行文件，
# 拒绝任意外部绝对路径被当作可信 CLI 加载。
_ALLOWED_CLI_BASENAMES = {"omnicrawl", "omnicrawl.exe"}


def _is_trusted_cli_path(path: Path) -> bool:
    if path.name not in _ALLOWED_CLI_BASENAMES:
        return False
    if is_frozen():
        return path.parent == application_dir()
    project_root = Path(__file__).resolve().parents[3]
    return (
        path == project_root / ".venv" / "Scripts" / path.name
        or path == project_root / ".venv" / "bin" / path.name
        or path.parent == project_root
    )


def _looks_like_path(value: str) -> bool:
    """判断 configured 是路径形式（含路径分隔符或扩展名）而非裸命令名。"""
    return bool(
        value
        and (
            "/" in value
            or "\\" in value
            or os.sep in value
            or value.endswith((".exe", ".bat", ".cmd", ".sh"))
        )
    )


def resolve_cli_candidates(configured: str = "omnicrawl") -> tuple[str, list[str]]:
    """返回 (选中命令, 已尝试候选路径列表)——供失败消息展示（F54）。

    B05-017：configured 作为文件路径命中时，必须通过信任白名单校验
    （名称 + 位置），防止配置被篡改为指向任意可执行文件。
    """
    companion = bundled_cli_path()
    if companion is not None:
        return str(companion), [str(companion)]

    configured_path = Path(configured).expanduser()
    if configured_path.is_file():
        if _is_trusted_cli_path(configured_path):
            return str(configured_path.resolve()), [str(configured_path.resolve())]
        logger.warning("configured CLI 不在信任白名单内，忽略: %s", configured_path)
        configured = "omnicrawl"
    elif _looks_like_path(configured):
        # 配置为路径形式（含分隔符）但文件不存在/不可信 → 回退默认命令名，
        # 绝不把外部路径字符串当作命令返回（B05-017）。
        logger.warning("configured CLI 是路径但不可信/不存在，回退默认: %s", configured)
        configured = "omnicrawl"

    discovered = shutil.which(configured)
    candidates = [configured]
    if discovered:
        return discovered, [discovered, configured]

    # 源码模式下自动探测项目根目录的 .venv 入口脚本（项目根 = core 的上级两级）。
    # Windows venv 用 Scripts/，POSIX 用 bin/（P9-C 修 macOS 探测失败）。
    if not is_frozen():
        suffix = ".exe" if sys.platform == "win32" else ""
        venv_root = Path(__file__).resolve().parents[3] / ".venv"
        for subdir in ("Scripts", "bin"):
            venv_cli = venv_root / subdir / f"omnicrawl{suffix}"
            if venv_cli.is_file():
                return str(venv_cli), candidates + [str(venv_cli)]

    return configured, candidates


def resolve_cli_command(configured: str = "omnicrawl") -> str:
    """Resolve the CLI, preferring the companion executable in frozen builds."""
    return resolve_cli_candidates(configured)[0]


def document_candidates(names: Iterable[str]) -> list[Path]:
    """Build ordered candidate paths for externally shipped documentation."""
    roots = [application_dir()]
    if is_frozen():
        roots.append(portable_data_root())
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        roots.append(Path(bundle_root))

    candidates: list[Path] = []
    for root in roots:
        for name in names:
            candidates.extend((root / "docs" / name, root / name))
    return candidates


def find_document(*names: str) -> Path | None:
    """Return the first existing document with one of the supplied names."""
    for candidate in document_candidates(names):
        if candidate.is_file():
            return candidate
    return None
