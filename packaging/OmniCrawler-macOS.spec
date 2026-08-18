# -*- mode: python ; coding: utf-8 -*-
#
# macOS 便携包 PyInstaller 规格（Standard 范围：GUI + CLI + worker + Chromium）。
# 构建逻辑见 build_macos.sh。与 Linux 规格的差异：
#   - GUI 以 .app bundle 交付（BUNDLE），CLI/worker 两个 console 入口放进
#     Contents/MacOS，保持"双击 GUI / 终端用 CLI"两种启动方式。
#   - 不打包 OCR 运行时：Tesseract 由 Homebrew 提供，PaddleOCR 模型按需下载。

from pathlib import Path
import sys
import tomllib

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata


project_root = Path(SPECPATH).parent
src_root = project_root / "src"
packaging_root = project_root / "packaging"
sys.path.insert(0, str(src_root))

# B12-004：bundle 版本从 pyproject.toml 读取，不再硬编码（防发布时 info.plist
# 与包版本漂移）。bump_version.py 更新版本时 spec 无需手工同步。
_bundle_version = str(
    tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
)

datas = [
    (str(src_root / "omnicrawler" / "templates"), "omnicrawler/templates"),
    (str(src_root / "omnicrawler" / "gui" / "templates"), "omnicrawler/gui/templates"),
    (str(src_root / "omnicrawler" / "gui" / "help"), "omnicrawler/gui/help"),
    (str(src_root / "omnicrawler" / "fetching" / "stealth.min.js"), "omnicrawler/fetching"),
    (str(project_root / "plugins"), "plugins"),
    (str(project_root / "locale"), "omnicrawler/locale"),
]
hiddenimports = sorted(set(collect_submodules("omnicrawler") + collect_submodules("keyring.backends")))
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
# CLI exe 名不能用 omnicrawler（与 GUI OmniCrawler 在 APFS 大小写不敏感下冲突，
# 同 Windows；见 OmniCrawler.spec 注释）。用 omnicrawler-cli 区分。
cli_exe = EXE(
    cli_pyz, cli_analysis.scripts, [], exclude_binaries=True, name="omnicrawler-cli",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True,
    disable_windowed_traceback=False,
)

worker_analysis = Analysis([str(packaging_root / "worker_entry.py")], **common)
worker_pyz = PYZ(worker_analysis.pure)
worker_exe = EXE(
    worker_pyz, worker_analysis.scripts, [], exclude_binaries=True,
    name="omnicrawler-worker", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=True, disable_windowed_traceback=False,
)

# macOS 标准结构：COLLECT 收集全部 EXE + binaries + datas，再由 BUNDLE 包装成 .app。
# 直接 BUNDLE(3×EXE) 会缺 Contents/Frameworks（Python.framework 属 binaries 不会被
# 放置）→ PYI-5670/PYI-43699（2026-08-16 v0.9.0/v0.9.1 CI 确认）。
coll = COLLECT(
    gui_exe, cli_exe, worker_exe,
    gui_analysis.binaries, cli_analysis.binaries, worker_analysis.binaries,
    gui_analysis.datas, cli_analysis.datas, worker_analysis.datas,
    strip=False, upx=False, name="OmniCrawler",
)

app = BUNDLE(
    coll,
    name="OmniCrawler.app",
    icon=None,
    bundle_identifier="com.omnicrawler.desktop",
    info_plist={
        "CFBundleName": "OmniCrawler",
        "CFBundleDisplayName": "OmniCrawler",
        "CFBundleShortVersionString": _bundle_version,
        "CFBundleVersion": _bundle_version,
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        "NSHumanReadableCopyright": "AGPL-3.0-only",
    },
)
