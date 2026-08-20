# -*- mode: python ; coding: utf-8 -*-
#
# macOS 便携包 PyInstaller 规格（Full 范围：GUI + CLI + worker + Chromium
# + cv2/selenium）。构建逻辑见 build_macos.sh。
#
# 与 OmniCrawler-macOS.spec（Standard）的差异：
#   - 不 excludes cv2/selenium，改为 collect_all 显式收集；
#   - **保留 paddle/paddleocr/paddlex 的 excludes**：macOS 无稳定 paddle
#     wheel（Intel 长期不支持、M 系需显式 paddlepaddle-macos 且仍不完整），
#     强行 collect 会在构建期 import 失败。macOS Full 的 OCR 推理走
#     PaddleOCR 3.x Transformers 后端（见方案 5.3 M3），不打包 paddle；
#   - BUNDLE 显式传 datas（对齐 P9 风格一致性，消除 BUNDLE 自动收集隐患）。
# OCR 运行时（Tesseract 二进制/tessdata/ChromeDriver）由 prepare_macos_runtime.sh
# 制备并打进 .app/Contents/MacOS/runtime（= application_dir()/runtime，
# 匹配 runtime_paths.py:166）。

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
binaries = []
hiddenimports = collect_submodules("omnicrawler")
excludes = [
    # macOS 无稳定 paddle wheel：即使 Full 也不打包 paddle 系（Transformers 后端替代）
    "paddle", "paddleocr", "paddlex", "torch", "torchvision",
    "pyarrow", "duckdb", "scrapy", "redis", "psycopg", "opensearchpy",
]

# cv2/selenium/lxml/playwright 在运行期做静态扫描看不到的 import，显式收集
# （镜像 Windows OmniCrawler.spec:25-32 的写法；paddle 系因无 wheel 除外）。
for package in ("cv2", "selenium", "lxml", "playwright"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

for package in ("keyring.backends",):
    hiddenimports += collect_submodules(package)

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
        "NSHumanReadableCopyright": "Apache-2.0",
    },
)
