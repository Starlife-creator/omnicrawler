# -*- mode: python ; coding: utf-8 -*-
#
# Linux 便携包 PyInstaller 规格（Standard 范围：GUI + CLI + worker + Chromium）。
# 与 Windows 便携包不同，Linux 不做 Full OCR 运行时打包：Tesseract 由系统包
# （apt/dnf）提供，PaddleOCR 模型按需用 tools/download_ocr_models.py 下载到
# 用户目录。构建逻辑见 build_linux.sh。
#
# 本 spec 沿用 packaging/OmniCrawler-Standard.spec 的 datas / excludes 取舍：
# 排除 paddle / torch 等重型可选包，保留 lxml 与 playwright 的显式收集。

from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata


project_root = Path(SPECPATH).parent
src_root = project_root / "src"
packaging_root = project_root / "packaging"
sys.path.insert(0, str(src_root))

datas = [
    (str(src_root / "omnicrawler" / "templates"), "omnicrawler/templates"),
    (str(src_root / "omnicrawler" / "gui" / "templates"), "omnicrawler/gui/templates"),
    (str(src_root / "omnicrawler" / "gui" / "help"), "omnicrawler/gui/help"),
    (str(src_root / "omnicrawler" / "fetching" / "stealth.min.js"), "omnicrawler/fetching"),
    # 用户插件工作目录：打包进便携版，用户可在便携环境里放自己的插件
    (str(project_root / "plugins"), "plugins"),
    # 语言包：i18n._find_localedir 沿包父链找到 omnicrawler/locale（S42 打包登记）
    (str(project_root / "locale"), "omnicrawler/locale"),
]
hiddenimports = sorted(set(collect_submodules("omnicrawler") + collect_submodules("keyring.backends")))
excludes = [
    "paddle", "paddleocr", "paddlex", "cv2", "torch", "torchvision",
    "pyarrow", "duckdb", "scrapy", "redis", "selenium", "psycopg", "opensearchpy",
]

# lxml 的 C 扩展必须显式收集，静态扫描会漏掉共享库（与 Windows spec 同源）。
_lxml_datas, _lxml_binaries, _lxml_hidden = collect_all("lxml")
datas += _lxml_datas
datas += copy_metadata("lxml")
# Playwright 的 driver（node + JS）必须显式收集；patchright hook 只收 patchright 自身。
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
    cli_pyz, cli_analysis.scripts, [], exclude_binaries=True, name="omnicrawler",
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

bundle = COLLECT(
    gui_exe, cli_exe, worker_exe, gui_analysis.binaries, gui_analysis.datas,
    cli_analysis.binaries, cli_analysis.datas, worker_analysis.binaries,
    worker_analysis.datas, strip=False, upx=False, name="OmniCrawler",
)
