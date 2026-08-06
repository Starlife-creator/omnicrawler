# Windows 便携版构建

## 版本号来源

构建脚本 **不会从命令行参数接收版本号**。它直接从源码读取：

```
src/omnicrawl/__init__.py  →  __version__
           ↓
build_windows.ps1（启动时立刻打印版本、产物命名使用该值）
           ↓
OmniCrawler-{version}-Windows-Portable-{edition}.zip
```

**因此：构建前不要改版本号。** 版本号就是你当前在开发的那个版本。
如果要变更版本号，用 `tools/bump_version.py` 独立操作，绝不在构建流程中混着改。

---

## 产物结构

构建脚本按当前项目版本和 Edition 生成 Windows Portable ZIP；不要手工依赖旧的固定文件名。

- `OmniCrawler.exe`：无控制台图形工作台。
- `omnicrawl.exe`：带控制台的完整 CLI 引擎，GUI 也通过它执行任务。
- `_internal/`：两个入口共享的 Python 与全部功能模块。
- `browsers/`：Playwright Chromium。
- `runtime/selenium/`：与包内 Chromium 主版本匹配的 ChromeDriver。
- `runtime/tesseract/`：Tesseract 引擎、必需 DLL、中文/英文/方向模型。
- `runtime/models/paddlex/`：PPStructureV3 的全部离线模型及校验清单。
- `configs/`、`examples/`、`docs/`：可编辑配置、示例和本地帮助。

这是文件夹式便携应用。两个 EXE 共享 `_internal`，既保留 GUI/CLI 不同控制台行为，
又避免把 1 GB 级依赖重复塞进两个单文件 EXE。不得单独复制任意一个 EXE。

## 一键构建

在 64 位 Windows、Python 3.10+ 环境运行：

```powershell
.\build_windows.ps1
```

脚本在 `%TEMP%\OmniCrawler-build-<edition>`（隔离 venv 在 `%TEMP%\OmniCrawler-build-<edition>-venv`）创建隔离环境并按顺序执行：

1. 安装 `.[full,dev]`、PyInstaller 和构建工具。
2. 下载 Playwright Chromium。
3. 解析浏览器版本并下载匹配 ChromeDriver。
4. 纯提取 Tesseract 5，下载 `chi_sim/eng/osd`；不写注册表或系统 PATH。
5. 下载并实际推理验证 PPStructureV3 全部模型。
6. 构建共享运行时 GUI/CLI、生成 SBOM/能力清单、执行产物级测试。
7. 生成 ZIP 与 SHA-256。

已存在且验证过的缓存会复用。只有明确传入跳过参数时才跳过下载；发布构建不应使用
跳过参数。

## 完全离线重建

当依赖、Chromium 和 Full 版运行时资产已经在本地准备好时，使用 `-Offline`，而不是手工修改旧的
便携目录。该模式不创建环境、不安装依赖、不下载浏览器或模型；它会先验证指定 Python 的依赖矩阵，
再将缓存复制到新的版本化暂存目录并执行完整打包校验。构建期间 LiteLLM 也被强制使用包内价格表，
避免可选导入触发在线刷新。

```powershell
$python = "$PWD\.venv\Scripts\python.exe"
$out = "$PWD\artifacts\release\0.4.0"

.\build_windows.ps1 -Offline -Edition Standard -BuilderPythonPath $python `
  -BuildRootPath "$PWD\artifacts\build\0.4.0-standard-r1" `
  -ReleaseOutputPath $out -BrowserCachePath "$PWD\build_cache\browsers"

.\build_windows.ps1 -Offline -Edition Full -BuilderPythonPath $python `
  -BuildRootPath "$PWD\artifacts\build\0.4.0-full-r1" `
  -ReleaseOutputPath $out -BrowserCachePath "$PWD\build_cache\browsers" `
  -RuntimeCachePath "$PWD\build_cache\runtime"
```

暂存目录中的 `release\OmniCrawler` 是与 ZIP 对应的完整文件夹。旧版本产物应保留为只读归档；
不要覆盖或向旧文件夹增量复制源码、EXE、`_internal` 或运行时资产。

## 发布前检查

```powershell
.\omnicrawl.exe --version
.\omnicrawl.exe --help
.\omnicrawl.exe capabilities --verify-imports
```

随后双击 `OmniCrawler-Launcher.bat`，验证第一步网址输入、两个“下一步”按钮、本地帮助、
简单/专业/开发者切换和“运行能力与自包含组件”。构建脚本还会自动执行 Playwright、
Selenium、Tesseract 与离线 Paddle 模型验证。

## Windows 包的范围

Windows 包不携带 Linux/macOS 启动脚本、包管理器缓存或其他平台二进制。跨平台源码、
命令和安装预案保留在源码包及 `docs/INSTALLATION.md` 中。

## 源码 ZIP 构建

源码 ZIP 由 `tools/build_source_archive.py` 独立生成，不与便携版共用构建流程。
wheel 为独立产物：构建环境（`.venv`）**未预装 `build` 模块**，不能用 `python -m build`，需用 `pip wheel` 单独构建。

```powershell
# 1) 源码 ZIP（由 build_source_archive.py 生成；--output-dir 必须与下方产物落点一致）
.\.venv\Scripts\python.exe tools\build_source_archive.py --output-dir artifacts\python\{version}

# 2) wheel（--no-deps 仅打包本项目；离线环境加 --no-build-isolation）
.\.venv\Scripts\python.exe -m pip wheel . --no-deps -w artifacts\python\{version}
```

产物落点：
- `artifacts/python/{version}/OmniCrawler-{version}-Source.zip`（源码归档，由 `build_source_archive.py` 产出）
- `artifacts/python/{version}/omnicrawl_platform-{version}-py3-none-any.whl`（wheel，由上方 `pip wheel` 生成，**非** `build_source_archive.py` 产出）

## 完整产物一览

每次构建共 4 类产物，路径规则详见 [`artifacts/README.md`](../artifacts/README.md)：

| # | 产物 | 路径模式 |
|---|------|---------|
| 1 | Standard 便携 ZIP | `artifacts/release/{version}/OmniCrawler-{version}-Windows-Portable-Standard.zip` |
| 2 | Full 便携 ZIP | `artifacts/release/{version}/OmniCrawler-{version}-Windows-Portable-Full.zip` |
| 3 | 源码 ZIP + wheel | `artifacts/python/{version}/OmniCrawler-{version}-Source.zip` |
| 4 | 完整便携目录（未压缩完整包） | `artifacts/build/{version}-{edition}-rN/release/OmniCrawler/` |

> **关于"完整便携目录"（第 4 类产物）**：它**不是独立命令**，而是由运行 Standard（`-Edition Standard`）或 Full（`-Edition Full`）便携构建时**自动产出并保留的未压缩目录**——脚本先构建到该目录，再将其压缩为对应的 `OmniCrawler-{version}-Windows-Portable-{edition}.zip`（第 1/2 类）。构建结束后此目录不会被删除，可直接作为"压缩前的完整包"使用或分发。其中 `{version}-{edition}-rN` 的 `rN` 为构建轮次（如 `0.4.0-standard-r1`），与 `-BuildRootPath` 参数值一致。
