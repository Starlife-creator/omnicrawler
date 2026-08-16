# -*- mode: python ; coding: utf-8 -*-
#
# Linux 便携包 PyInstaller 规格（Full 范围：GUI + CLI + worker + Chromium
# + paddle/paddleocr/paddlex/cv2/selenium）。构建逻辑见 build_linux.sh。
#
# 与 OmniCrawler-Linux.spec（Standard）的差异：不 excludes 重型可选包，
# 改为 collect_all 显式收集（镜像 Windows OmniCrawler.spec 的写法），使
# Full 产物真含 paddle/paddleocr/selenium 能力。OCR 运行时（Tesseract
# 二进制/tessdata/ChromeDriver/Paddle 模型）由 prepare_linux_runtime.sh
# 制备并打进 application_dir()/runtime，运行时经 configure_runtime_environment()
# 探测（runtime_paths.py:166）。

from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata


project_root = Path(SPECPATH).parent
src_root = project_root / "src"
packaging_root = project_root / "packaging"
sys.path.insert(0, str(src_root))

datas = [
    (str(src_root / "omnicrawl" / "templates"), "omnicrawl/templates"),
    (str(src_root / "omnicrawl" / "gui" / "templates"), "omnicrawl/gui/templates"),
    (str(src_root / "omnicrawl" / "gui" / "help"), "omnicrawl/gui/help"),
    (str(src_root / "omnicrawl" / "fetching" / "stealth.min.js"), "omnicrawl/fetching"),
    # 用户插件工作目录：打包进便携版，用户可在便携环境里放自己的插件
    (str(project_root / "plugins"), "plugins"),
    # 语言包：i18n._find_localedir 沿包父链找到 omnicrawl/locale（S42 打包登记）
    (str(project_root / "locale"), "omnicrawl/locale"),
]
binaries = []
hiddenimports = collect_submodules("omnicrawl")
excludes = [
    # Standard 会排除这些重型包；Full 全部收集，仅排除非本平台可用的残余
    "torch", "torchvision", "pyarrow", "duckdb", "scrapy", "redis",
    "psycopg", "opensearchpy",
]

# PaddleOCR/PaddleX 与插件类包在运行期做静态扫描看不到的 import（镜像
# Windows OmniCrawler.spec:25-32）。模型权重放在 runtime/models 不进 EXE，
# 这里只收集代码、包数据与共享库。
for package in ("paddle", "paddleocr", "paddlex", "cv2", "selenium", "lxml", "playwright"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

# Paddle 的 paddle/libs/*.so（libpaddle.so 等）collect_all 可能漏收集（PaddleOCR
# discussion #11342：打包后 libs 文件比开发环境少，导致 frozen import 失败）。
# 显式收集全部动态库 + 把 libs 目录完整放进 datas 供运行时 _set_paddle_lib_path 找到。
_paddle_dyn = collect_dynamic_libs("paddle")
binaries += _paddle_dyn
datas += collect_data_files("paddle", includes=["libs/*"], include_py_files=False)

for package in ("keyring.backends",):
    hiddenimports += collect_submodules(package)

# PaddleX 通过 importlib.metadata 检查 OCR extra，PyInstaller 可能收集了
# 可 import 模块却漏掉发行元数据（镜像 Windows OmniCrawler.spec:37-48）。
for distribution in (
    "beautifulsoup4", "einops", "ftfy", "imagesize", "Jinja2",
    "latex2mathml", "lxml", "opencv-contrib-python", "openpyxl",
    "premailer", "pyclipper", "pypdfium2", "python-bidi", "regex",
    "safetensors", "scikit-learn", "scipy", "sentencepiece", "shapely",
    "tiktoken", "tokenizers",
):
    datas += copy_metadata(distribution)

hiddenimports = sorted(set(hiddenimports))

common = dict(
    pathex=[str(src_root)], binaries=binaries, datas=datas,
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

bundle = COLLECT(
    gui_exe, cli_exe, worker_exe, gui_analysis.binaries, gui_analysis.datas,
    cli_analysis.binaries, cli_analysis.datas, worker_analysis.binaries,
    worker_analysis.datas, strip=False, upx=False, name="OmniCrawler",
)
