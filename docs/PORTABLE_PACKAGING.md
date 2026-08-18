# 便携版构建（Windows / Linux / macOS）

本文是三平台便携包构建的**总纲**。各平台细节与历史沿革见：

- Windows：`docs/WINDOWS_PACKAGING.md`（build_windows.ps1，两 Edition，含 OCR 运行时打包）
- Linux：`build_linux.sh`（本仓库根目录）
- macOS：`build_macos.sh`（本仓库根目录）

## 版本号来源（所有平台统一）

构建脚本**不从命令行接收版本号**，直接从源码读取：

```
src/omnicrawler/__init__.py  →  __version__
           ↓
构建脚本启动时立刻读取并校验三重一致：
  ① omnicrawler.__version__
  ② pyproject.toml project.version
  ③ 构建 venv 中 installed 的 omnicrawler-platform 版本
           ↓
OmniCrawler-<version>-<Platform>-Portable-<Edition>.<ext>
```

**因此：构建前不要改版本号。** 版本号变更用 `tools/bump_version.py` 独立操作，绝不在构建流程中混着改。

## 平台矩阵

| 平台 | 脚本 | PyInstaller spec | 产物格式 | 运行时策略 |
|---|---|---|---|---|
| Windows | `build_windows.ps1` | `packaging/OmniCrawler[-Standard].spec` | ZIP（Standard/Full） | 捆绑 Chromium + ChromeDriver + Tesseract + PaddleOCR |
| Linux | `build_linux.sh` | `packaging/OmniCrawler-Linux.spec` | tar.gz（Standard/Full） | 捆绑 Chromium；Tesseract 由系统包（apt/dnf）提供 |
| macOS | `build_macos.sh` | `packaging/OmniCrawler-macOS.spec` | dmg（Standard/Full） | 捆绑 Chromium；Tesseract 由 Homebrew 提供 |

三个 spec 共享同一套 datas / hiddenimports / excludes 取舍（Standard 范围排除 paddle/torch 等重型可选包、显式收集 lxml 与 playwright driver）。改动跨平台行为时，**四个 spec 必须同步修改**。

## 平台差异说明

### Linux（build_linux.sh）

- 仅支持 x86_64 / aarch64（脚本内 `uname -m` 断言）。
- 在隔离 venv（`<BuildRoot>/venv`）安装 `.[full,dev]` 或 Standard extras 后构建。
- 产物为 `OmniCrawler-<version>-Linux-Portable-<Edition>.tar.gz`，解压后：
  - GUI：`OmniCrawler`
  - CLI：`omnicrawler`
  - worker：`omnicrawler-worker`
- Full 版的 OCR（Tesseract）依赖系统包：`tesseract-ocr tesseract-ocr-eng tesseract-ocr-chi-sim`（apt）或 `tesseract tesseract-langpack-*`（dnf）。
- 服务器无桌面时用 CLI，不需要启动 GUI。

### macOS（build_macos.sh）

- 仅能在 macOS 上运行（PyInstaller 不支持交叉编译）。
- 用 `OmniCrawler-macOS.spec`（BUNDLE）生成 `.app`，CLI/worker 两个 console 入口进 `Contents/MacOS`。
- **ad-hoc 签名**：`codesign --force --deep --sign -`，无需开发者证书；但因未公证，Gatekeeper 会拦截首次打开——需右键 → 打开，或 `xattr -dr com.apple.quarantine`。
- 产物为 `OmniCrawler-<version>-macOS-Portable-<Edition>.dmg`；`hdiutil` 失败时回退 tar.gz。
- Tesseract 由 Homebrew 提供：`brew install tesseract tesseract-lang`。

## 发布链路

`release.yml` 的 `v*` tag 会并行触发三个构建 job（Windows/Linux/macOS），产物经 `actions/upload-artifact` 汇入聚合 `release` job，统一生成 provenance 并发布到 GitHub Release：

```
build-windows-portable ─┐
build-linux-portable   ─┼─▶ release（下载三平台产物 → provenance → 发布）
build-macos-portable   ─┘
```

各平台各自生成带后缀的校验和与 SBOM：`SHA256SUMS-<platform>.txt`、`omnicrawler-sbom-<platform>.cdx.json`。

## 构建自包含 vs 产物自包含

两个「自包含」目标需要区分，避免误读：

- **产物自包含（硬约束）**：打包后的便携 ZIP 运行时零外部依赖——Chromium、
  ChromeDriver、Tesseract、PaddleOCR 模型全部捆绑进包内。这是不可妥协的
  目标，CI 与本地构建都在满足它。
- **构建自包含（软目标）**：构建过程是否零网络。目前 Python wheel 依赖始终
  通过 `pip install` 联网安装；仅浏览器与 OCR 运行时等大资产支持本地缓存
  复用（`build_cache/browsers`、`build_cache/runtime`），并非「从压缩包解压 wheel」。

发布以 CI（`release.yml`）为主路径，联网构建；本地 `-Offline` 是 CI 不可用
时的兜底，复用缓存而非从压缩包解压依赖。

## 完全离线重建

Linux/macOS 脚本暂未提供与 Windows `-Offline` 完全等价的离线模式；离线场景先在本机构建好浏览器缓存（`PLAYWRIGHT_BROWSERS_PATH`），用 `--skip-browser-download` 传入。

## 产物归档

三平台产物统一落点见 `artifacts/README.md`（`artifacts/release/<version>/`）。源码 ZIP 与 wheel 由 `tools/build_source_archive.py` + `pip wheel` 独立生成，与便携版构建无关。
