# -*- mode: python ; coding: utf-8 -*-

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
    # 语言包：i18n._find_localedir 沿包父链找到 omnicrawler/locale（S42 打包登记）
    (str(project_root / "locale"), "omnicrawler/locale"),
]
binaries = []
hiddenimports = collect_submodules("omnicrawler")

# PaddleOCR/PaddleX and plugin-based packages perform runtime imports that a
# static scan cannot completely see. Their model weights stay outside the EXE
# under runtime/models, so this only collects code, package data and DLLs.
for package in ("paddle", "paddleocr", "paddlex", "cv2", "selenium", "lxml", "playwright"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

for package in ("keyring.backends", "scrapy", "twisted.plugins"):
    hiddenimports += collect_submodules(package)

# scipy._external.array_api_compat 是 scipy 内嵌（vendored）的 array_api_compat，
# 由构建期脚本生成，PyInstaller 的 collect_submodules("scipy") 静态扫描看不到
# 其内部子模块（paddleocr import 时报 'No module named
# scipy._external.array_api_compat.numpy.fft'，v0.9.1 Windows CI 实测）。
# 显式按 vendored 子树收集；若模块不存在，collect_submodules 返回空不中断构建。
hiddenimports += collect_submodules("scipy")
hiddenimports += collect_submodules("scipy._external.array_api_compat")
hiddenimports += collect_submodules("array_api_compat")

# PaddleX checks its OCR extra through importlib.metadata before creating a
# pipeline. PyInstaller may collect the importable modules while omitting their
# distribution metadata, which would make a complete offline build look
# incomplete at runtime.
for distribution in (
    "beautifulsoup4", "einops", "ftfy", "imagesize", "Jinja2",
    "latex2mathml", "lxml", "opencv-contrib-python", "openpyxl",
    "premailer", "pyclipper", "pypdfium2", "python-bidi", "regex",
    "safetensors", "scikit-learn", "scipy", "sentencepiece", "shapely",
    "tiktoken", "tokenizers",
):
    datas += copy_metadata(distribution)

hiddenimports = sorted(set(hiddenimports))

# Two one-folder programs share one COLLECT directory. This preserves the
# windowed GUI and console CLI while keeping a single copy of large DLL/data
# dependencies under _internal.
common = dict(
    pathex=[str(src_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "pytest", "torch", "torchvision"],
    noarchive=False,
)

gui_analysis = Analysis([str(packaging_root / "gui_entry.py")], **common)
gui_pyz = PYZ(gui_analysis.pure)
gui_exe = EXE(
    gui_pyz,
    gui_analysis.scripts,
    [],
    exclude_binaries=True,
    name="OmniCrawler",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

cli_analysis = Analysis([str(packaging_root / "cli_entry.py")], **common)
cli_pyz = PYZ(cli_analysis.pure)
cli_exe = EXE(
    cli_pyz,
    cli_analysis.scripts,
    [],
    exclude_binaries=True,
    name="omnicrawler",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
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
    gui_exe,
    cli_exe,
    worker_exe,
    gui_analysis.binaries,
    gui_analysis.datas,
    cli_analysis.binaries,
    cli_analysis.datas,
    worker_analysis.binaries,
    worker_analysis.datas,
    strip=False,
    upx=False,
    name="OmniCrawler",
)
