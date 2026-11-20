# 便携版构建（Windows / Linux / macOS）

> 适用版本：1.0.24 · 配置协议：v5 · 维护状态：现行（历史状态见归档；功能验证以验收账本为准）

## 发行规则：只构建 Standard（2026-11-19 定案）

**对外只发 Standard 一个包。不再构建 Full 大号冻结包。**

| 项 | Standard |
|---|---|
| 公开 Release | ✅ **唯一产物** |
| CI 构建 | ✅ 每次照常 |
| CI 冒烟 | ✅ 归档级 + 运行时 |
| 组件验证 | ✅ **装齐市场组件并逐个跑通** |

### 为什么不再构建 Full

原先的做法是「停发不停建」——Full 不挂 Release 但每次照常构建，理由是
它是 PyInstaller 冻结兼容性的试金石。**维护者 2026-11-19 裁定改为不构建**，
理由成立：

1. **代价高**：每平台多跑一轮完整 PyInstaller（Linux 实测多约 40 分钟），
   三平台累计一个多小时，而它产出的包没人用；
2. **证明的不是用户路径**：它验证「所有重依赖在同一个冻结包里能共存」，
   但用户拿到的从来不是那个包——用户拿到的是 **Standard + 按需装组件**；
3. **能力对账是错误判据**：拿两份静态能力 JSON 比数字，报出的缺口大多是
   `full` 档能力（`paddle_models` / `selenium` / `crawl4ai` / `s3`），
   那些本就不该由 Standard 承担。**比数字只能证明「声明齐了」，
   与「装完能用」不是一回事。**

### 现在验证什么

`tools/portable/verify_standard_components.py`，在解压后的真实 Standard 产物上：

1. 按市场快照逐个 `components install-recipe --download`（真实从上游拉构件）；
2. 对每个装好的组件执行 `components run`，确认它在**隔离子进程里真的跑起来**；
3. 任何一步失败都让 CI 红，并指名**哪个组件、哪个阶段**。

判据是确定性的：组件必须**全部装上**、**全部跑通**；空组件列表判失败
（空集合会让"全部通过"恒真）。

### 冒烟也改为 Standard

`portable_archive_smoke.py` 现在用 `--edition Standard`，跑归档级 + 运行时冒烟。
不再需要同时解压两个 edition。

### 与旧流程的关系

历史模块 `standard_equivalence.py` 与 `collect_capability_reports.py`
**保留在仓库里**（项目纪律不用 `git rm`），但已不再接入任何 workflow。
守卫 `tests/unit/tools/test_standard_equivalence.py` 会断言
「旧的 `compare-capabilities` 步骤与 `--edition Full` 都不得回来」。

本文是三平台便携包构建的**总纲**。各平台细节与历史沿革见：

- Windows：`docs/WINDOWS_PACKAGING.md`（build_windows.ps1，两 Edition，含 OCR 运行时打包）
- Linux：`build_linux.sh`（本仓库根目录）
- macOS：`build_macos.sh`（本仓库根目录）

## 版本号来源（所有平台统一）

构建脚本**不从命令行接收版本号**，直接从源码读取：

```
src/omnicrawler/_version.py  →  __version__
           ↓
构建脚本启动时立刻读取并校验三重一致：
  ① omnicrawler.__version__（对 _version.py 的惰性 re-export，不是第二份真源）
  ② pyproject.toml project.version
  ③ 构建 venv 中 installed 的 omnicrawler-platform 版本
           ↓
OmniCrawler-<version>-<Platform>-Portable-<Edition>.<ext>
```

**因此：构建前不要改版本号。** 版本号变更用 `tools/releasing/bump_version.py` 独立操作，绝不在构建流程中混着改。

## 平台矩阵

| 平台 | 脚本 | PyInstaller spec | 产物格式 | 运行时策略 |
|---|---|---|---|---|
| Windows | `build_windows.ps1` | `packaging/OmniCrawler[-Standard].spec` | ZIP（Standard/Full） | 捆绑 Chromium + ChromeDriver + Tesseract + PaddleOCR |
| Linux | `build_linux.sh` | `packaging/OmniCrawler-Linux.spec` | tar.xz（Standard/Full） | 捆绑 Chromium；Tesseract 由系统包（apt/dnf）提供 |
| macOS | `build_macos.sh` | `packaging/OmniCrawler-macOS.spec` | dmg（Standard/Full） | 捆绑 Chromium；Tesseract 由 Homebrew 提供 |

三个 spec 共享同一套 datas / hiddenimports / excludes 取舍（Standard 范围排除 paddle/torch 等重型可选包、显式收集 lxml 与 playwright driver）。改动跨平台行为时，**四个 spec 必须同步修改**。

## 平台差异说明

### Linux（build_linux.sh）

- 仅支持 x86_64 / aarch64（脚本内 `uname -m` 断言）。
- 在隔离 venv（`<BuildRoot>/venv`）按 edition 安装运行时 extras + `build` extra（PyInstaller 打包工具；测试/lint 工具留在 `dev`，不进生产构建环境）后构建。
- 产物为 `OmniCrawler-<version>-Linux-Portable-<Edition>.tar.xz`，解压后：
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

`release.yml` 是薄编排层：完成版本与快速门禁后，并行调用三个本地 reusable workflow。平台产物经 `actions/upload-artifact` 汇入最终 reusable workflow，统一执行 provenance、供应链完整性检查和发布：

```
release.yml
  ├─ reusable-build-windows.yml ─┐
  ├─ reusable-build-linux.yml   ─┼─▶ reusable-finalize-release.yml
  └─ reusable-build-macos.yml   ─┘   （下载产物 → 安全门禁 → 发布）
```

调用边界使用显式 `workflow_call` inputs 传递 Python 版本与单资产体积上限；构建 job 只获得 `contents: read`、`id-token: write` 和 `attestations: write`，最终发布 job 才获得 `contents: write`。可选 GPG 密钥逐项传递，不使用 `secrets: inherit`。

各平台各自生成带后缀的校验和与 SBOM：`SHA256SUMS-<platform>.txt`、`omnicrawler-sbom-<platform>.cdx.json`。

## 构建自包含 vs 产物自包含

两个「自包含」目标需要区分，避免误读：

- **产物自包含（硬约束）**：打包后的便携 ZIP 运行时零外部依赖——Chromium、
  ChromeDriver、Tesseract、PaddleOCR 模型全部捆绑进包内。这是不可妥协的
  目标，CI 与本地构建都在满足它。
- **构建自包含（软目标）**：构建过程是否零网络。目前 Python wheel 依赖始终
  通过 `pip install` 联网安装；仅浏览器与 OCR 运行时等大资产支持本地缓存
  复用（`build_cache/browsers`、`build_cache/runtime`），并非「从压缩包解压 wheel」。

发布以 CI（`release.yml` 及其 reusable workflows）为主路径，联网构建；本地 `-Offline` 是 CI 不可用
时的兜底，复用缓存而非从压缩包解压依赖。

## 完全离线重建

Linux/macOS 脚本暂未提供与 Windows `-Offline` 完全等价的离线模式；离线场景先在本机构建好浏览器缓存（`PLAYWRIGHT_BROWSERS_PATH`），用 `--skip-browser-download` 传入。

## 产物归档

三平台产物统一落点见 `artifacts/README.md`（`artifacts/release/<version>/`）。源码 ZIP 与 wheel 由 `tools/builders/build_source_archive.py` + `pip wheel` 独立生成，与便携版构建无关。
