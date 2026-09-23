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
    (str(src_root / "omnicrawler" / "gui" / "branding"), "omnicrawler/gui/branding"),
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
    # ★ W4.1（2026-09-16，CI 实测）：**排除 nltk**。它是 `crawl4ai` 的依赖、产品源码零引用，
    #   但 PyInstaller 会为它装一个**启动运行时钩子**（pyi_rth_nltk）—— 该钩子在冻结包里
    #   一启动就抛 `AttributeError: http.client has no attribute HTTPSConnection`，
    #   导致 **打出来的 app 任何调用都起不来**（macOS 构建的冒烟就这样崩的）。
    #   排除它即可移除钩子；冻结包是否仍能 HTTPS，由构建脚本里既有的
    #   `capabilities --verify-imports` 冒烟回答（ssl 不可用会当场失败）。
    "nltk",
    # ★ Q1 试点（2026-09-23 维护者拍板）：QML 橱窗页保留**代码**、发布包**不打包**——
    #   排除三个 QML 模块后，PyInstaller 不再收集 Qt6Qml/Qt6Quick/Qt6QuickWidgets/
    #   Qt6ShaderTools DLL 与 PySide6/qml 目录（合计 ≈45 MB）。冻结包里橱窗页
    #   显式降级（qml_showcase.qml_available 判 ImportError 或页面文件缺失）。
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets",
]

# lxml's C-extension (etree) must be explicitly collected for PyInstaller;
# a static scan alone misses the shared library, causing ImportError at runtime.
_lxml_datas, _lxml_binaries, _lxml_hidden = collect_all("lxml")
datas += _lxml_datas
datas += copy_metadata("lxml")
# Playwright's driver (node.exe + JS) must be explicitly collected; the
# patchright hook only collects patchright data, not playwright's own driver.
_pw_datas, _pw_binaries, _pw_hidden = collect_all("playwright")
datas += _pw_datas
hiddenimports = sorted(set(hiddenimports + _pw_hidden))
hiddenimports = sorted(set(hiddenimports + _lxml_hidden))

# ★ W4.1（2026-09-16，CI 实测 macOS 便携包启动即崩）：
#   `nltk/pathsec.py` 在**模块顶层**用 `http.client.HTTPSConnection`，而该名字
#   **只在 `ssl` 能导入时才由 CPython 定义** ⇒ 冻结包里缺 `ssl`/`_ssl` 时，
#   PyInstaller 的 nltk 运行时钩子（pyi_rth_nltk）一启动就抛 AttributeError、整个 app 起不来。
#   显式声明：三平台一起修（Win/Linux 的构建 job 只构建、不启动产物，所以此前只有 macOS 暴露）。
hiddenimports = sorted(set(hiddenimports + ["ssl", "_ssl"]))

common = dict(
    pathex=[str(src_root)], binaries=_lxml_binaries + _pw_binaries, datas=datas, hiddenimports=hiddenimports,
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=excludes, noarchive=False,
)

gui_analysis = Analysis([str(packaging_root / "gui_entry.py")], **common)
gui_pyz = PYZ(gui_analysis.pure)
gui_exe = EXE(
    gui_pyz, gui_analysis.scripts, [], exclude_binaries=True, name="OmniCrawler",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False,
    disable_windowed_traceback=False,
    icon=str(src_root / "omnicrawler" / "gui" / "branding" / "omnicrawler.ico"),
)

cli_analysis = Analysis([str(packaging_root / "cli_entry.py")], **common)
cli_pyz = PYZ(cli_analysis.pure)
# CLI exe 名不能用 omnicrawler（与 GUI OmniCrawler.exe 在 Windows/macOS 大小写
# 不敏感文件系统上冲突，见 OmniCrawler.spec 注释）。用 omnicrawler-cli 区分。
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

bundle = COLLECT(
    gui_exe, cli_exe, worker_exe, gui_analysis.binaries, gui_analysis.datas,
    cli_analysis.binaries, cli_analysis.datas, worker_analysis.binaries, worker_analysis.datas,
    strip=False, upx=False, name="OmniCrawler",
)
