"""Runtime path helpers shared by source and frozen GUI builds."""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path


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
    expanded = value.replace("${APP_DIR}", str(application_dir())).replace("${DATA_DIR}", str(portable_data_root()))
    return Path(expanded).expanduser().resolve()


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
    browsers = app_dir / "browsers" if is_frozen() else runtime_dir / "browsers"
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
    tesseract = runtime_dir / "tesseract" / "tesseract.exe"
    tessdata = runtime_dir / "tesseract" / "tessdata"
    if tesseract.is_file():
        os.environ.setdefault("TESSERACT_CMD", str(tesseract))
        os.environ.setdefault("TESSDATA_PREFIX", str(tessdata))
    driver = runtime_dir / "selenium" / "chromedriver.exe"
    if driver.is_file():
        os.environ.setdefault("OMNICRAWL_SELENIUM_DRIVER", str(driver))
    chrome = bundled_browser_executable()
    if chrome is not None:
        os.environ.setdefault("OMNICRAWL_CHROME_BINARY", str(chrome))
    if is_frozen():
        os.environ.setdefault("OMNICRAWL_PORTABLE_ROOT", str(portable_data_root()))


def bundled_browser_available() -> bool:
    """Return whether a usable Playwright Chromium is shipped with the app."""
    return bundled_browser_executable() is not None


def bundled_browser_executable() -> Path | None:
    """Return the bundled Chromium executable, if present."""
    app_dir = application_dir()
    browsers = app_dir / "browsers" if is_frozen() else app_dir / ".runtime" / "browsers"
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


def resolve_cli_command(configured: str = "omnicrawl") -> str:
    """Resolve the CLI, preferring the companion executable in frozen builds."""
    companion = bundled_cli_path()
    if companion is not None:
        return str(companion)

    configured_path = Path(configured).expanduser()
    if configured_path.is_file():
        return str(configured_path.resolve())

    discovered = shutil.which(configured)
    return discovered or configured


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
