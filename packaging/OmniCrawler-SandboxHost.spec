# -*- mode: python ; coding: utf-8 -*-
"""Phase 2a C1：最小沙箱宿主 exe（omnicrawler-sandbox-host）。

契约 2 插件子进程的唯一生产后端。设计约束（方案 C1）：
- **不打包 omnicrawler 与任何第三方依赖**——bundle 构成即隔离的第一道保证；
  portable_smoke_test 断言宿主内 `import omnicrawler` 必失败。
- onefile：在 Windows AppContainer 沙箱内自解压于 AC\\Temp（PyInstaller ≥6.3
  bootloader 已 AppContainer-aware，pyinstaller #8291/#8290）。
- 入口 plugin_subprocess.py 仅依赖标准库（import json/sys/importlib/traceback）。
- 模式同 omnicrawler-worker.exe：随主便携包同目录发布、同版本对齐。

构建要求：PyInstaller ≥6.3（AppContainer-aware bootloader）。
"""

from pathlib import Path

project_root = Path(SPECPATH).parent
plugin_host = project_root / "src" / "omnicrawler" / "plugins" / "plugin_subprocess.py"

# 显式不收集任何 hiddenimport/datas/binaries——最小 bundle 是安全属性，
# 任何收集行为都会破坏"宿主内 import omnicrawler 必失败"的验收断言。
analysis = Analysis(
    [str(plugin_host)],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["omnicrawler"],
    noarchive=False,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="omnicrawler-sandbox-host",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # onefile 不压缩：沙箱内自解压行为更确定
    console=True,  # stdin/stdout JSON IPC，必须 console
    disable_windowed_traceback=False,
)
