# -*- mode: python ; coding: utf-8 -*-
#
# macOS 便携包 PyInstaller 规格（Standard 范围：GUI + CLI + worker + Chromium）。
# 构建逻辑见 build_macos.sh。与 Linux 规格的差异：
#   - GUI 以 .app bundle 交付（BUNDLE），CLI/worker 两个 console 入口放进
#     Contents/MacOS，保持"双击 GUI / 终端用 CLI"两种启动方式。
#   - 不打包 OCR 运行时：Tesseract 由 Homebrew 提供，PaddleOCR 模型按需下载。

from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata


project_root = Path(SPECPATH).parent
src_root = project_root / "src"
packaging_root = project_root / "packaging"
sys.path.insert(0, str(src_root))

datas = [
    (str(src_root / "omnicrawl" / "templates"), "omnicrawl/templates"),
    (str(src_root / "omnicrawl" / "gui" / "templates"), "omnicrawl/gui/templates"),
    (str(src_root / "omnicrawl" / "gui" / "help"), "omnicrawl/gui/help"),
    (str(src_root / "omnicrawl" / "fetching" / "stealth.min.js"), "omnicrawl/fetching"),
    (str(project_root / "plugins"), "plugins"),
    (str(project_root / "locale"), "omnicrawl/locale"),
]
hiddenimports = sorted(set(collect_submodules("omnicrawl") + collect_submodules("keyring.backends")))
excludes = [
    "paddle", "paddleocr", "paddlex", "cv2", "torch", "torchvision",
    "pyarrow", "duckdb", "scrapy", "redis", "selenium", "psycopg", "opensearchpy",
]

_lxml_datas, _lxml_binaries, _lxml_hidden = collect_all("lxml")
datas += _lxml_datas
datas += copy_metadata("lxml")
_pw_datas, _pw_binaries, _pw_hidden = collect_all("playwright")
datas += _pw_datas
hiddenimports = sorted(set(hiddenimports + _pw_hidden + _lxml_hidden))

common = dict(
    pathex=[str(src_root)], binaries=_lxml_binaries + _pw_binaries, datas=datas,
    hiddenimports=hiddenimports, hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=excludes, noarchive=False,
)

gui_analysis = Analysis([str(packaging_root / "gui_entry.py")], **common)
gui_pyz = PYZ(gui_analysis.pure)
gui_exe = EXE(
    gui_pyz, gui_analysis.scripts, [], exclude_binaries=True, name="OmniCrawler",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False,
    disable_windowed_traceback=False,
)

cli_analysis = Analysis([str(packaging_root / "cli_entry.py")], **common)
cli_pyz = PYZ(cli_analysis.pure)
cli_exe = EXE(
    cli_pyz, cli_analysis.scripts, [], exclude_binaries=True, name="omnicrawl",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True,
    disable_windowed_traceback=False,
)

worker_analysis = Analysis([str(packaging_root / "worker_entry.py")], **common)
worker_pyz = PYZ(worker_analysis.pure)
worker_exe = EXE(
    worker_pyz, worker_analysis.scripts, [], exclude_binaries=True,
    name="omnicrawl-worker", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=True, disable_windowed_traceback=False,
)

app = BUNDLE(
    gui_exe,
    cli_exe,
    worker_exe,
    name="OmniCrawler.app",
    icon=None,
    bundle_identifier="com.omnicrawler.desktop",
    info_plist={
        "CFBundleName": "OmniCrawler",
        "CFBundleDisplayName": "OmniCrawler",
        "CFBundleShortVersionString": "0.8.0",
        "CFBundleVersion": "0.8.0",
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        "NSHumanReadableCopyright": "AGPL-3.0-only",
    },
)
