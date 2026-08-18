# 构建与发布产物（便携包产物应是完全自包含应用，内置python）

## 版本来源（必读）

**构建产物的版本号唯一来源是 `src/omnicrawler/__init__.py` 中的 `__version__`。**
`build_windows.ps1` 启动时会自动读取，产物命名由脚本决定，严禁在构建
过程中手动修改版本号。版本号的变更是一个独立操作（通过 `tools/bump_version.py`），
绝对不与构建、修复、测试等其他操作混在一起。

---

`artifacts/` 是 OmniCrawler 的版本化本地交付物归档；它不是源码，也不应被源码 ZIP 再次打包。

```text
artifacts/
  build/<version>-<edition>-rN/release/OmniCrawler/  完整 Windows 便携目录
  release/<version>/                                Windows ZIP / Linux tar.gz / macOS dmg 与 SHA-256 清单
  python/<version>/                                 源码 ZIP 与 wheel
  package-source/                                   从源码 ZIP 解压出的 wheel 构建暂存
  tmp/                                              可恢复的短期构建临时文件
```

## 产物种类（完整列表）

每次构建生成 **4 类产物**，全部从同一个 `__version__` 派生命名：（构建产物时如果本地保留有之前的构建产物，优先复用依赖，不从网络下载）

| # | 产物类型 | 示例文件名 | 输出目录 |
|---|---|---|---|
| 1 | **Standard 便携包** | `OmniCrawler-0.8.0-Windows-Portable-Standard.zip` / `...-Linux-Portable-Standard.tar.gz` / `...-macOS-Portable-Standard.dmg` | `artifacts/release/{version}/` |
| 2 | **Full 便携包** | `OmniCrawler-0.8.0-Windows-Portable-Full.zip` / 同规则 Linux / macOS | `artifacts/release/{version}/` |
| 3 | **源码 ZIP** | `OmniCrawler-0.8.0-Source.zip` | `artifacts/python/{version}/` |
| 4 | **完整便携目录**（压缩前）| `release/OmniCrawler/` | `artifacts/build/{version}-{edition}-rN/` |

- #4 是 #1/#2 的压缩前完整文件夹（Windows 直接可运行；Linux/macOS 见 `docs/PORTABLE_PACKAGING.md`），可直接运行、调试。
- #3 是纯源码归档（不含 `artifacts/`、`build_cache/`、`dist/` 等构建物）。
- wheel（`omnicrawler_platform-0.8.0-py3-none-any.whl`）随 #3 同一目录产出。
- 三平台便携包构建总纲见 `docs/PORTABLE_PACKAGING.md`；Windows 细节见 `docs/WINDOWS_PACKAGING.md`。

## 历史归档

当前已归档的版本：

- `2.3.1`：历史 Standard/Full 便携目录、两个 ZIP、校验清单、源码 ZIP 和 wheel。
- `2.6.0`：Standard/Full 便携目录、两个 ZIP、校验清单、源码 ZIP 和 wheel。
- `2.7.0`：当前离线构建的 Standard/Full 便携 ZIP（664MB / 1.9GB）、SHA-256 校验清单、
  源码 ZIP（6.7MB）和 wheel（602KB）。两个完整便携目录位于
  `artifacts/build/2.7.0-standard-r1/release/OmniCrawler/` 和
  `artifacts/build/2.7.0-full-r1/release/OmniCrawler/`。

使用 ZIP 前，先在其版本目录中读取 `SHA256SUMS*.txt`，并用 PowerShell 的
`Get-FileHash -Algorithm SHA256` 核对。完整目录与 ZIP 是同一版本的两种交付形式；
不要交叉复制其中的 EXE、`_internal`、浏览器或运行时资产。

根目录的 `release/` 与 `dist/` 仅为兼容旧构建工具的默认输出位置。新的可保留发布物应
显式输出至本目录的版本化路径；`build/`、`build_dist/` 和 `build_cache/` 是构建中间物或
缓存，不属于版本发行归档。

## 单一输出规则（F53，必须遵守）

- **可保留发布物的唯一归档位置是 `artifacts/` 下的版本化目录**（`build/`、`release/`、`python/`）。
- **`dist/` 只允许作为 CI（quality.yml）的临时构建工作区**：CI 每次从全新 checkout 生成并
  校验产物后即丢弃，不允许在其中留存长期发布物。手工构建请使用
  `build_windows.ps1`（输出至 `artifacts/`），不要手工把文件放进 `dist/` 或根目录 `release/`。
- 若发现 `dist/`、根目录 `release/` 或 `build/` 出现可保留产物，视为漂移，应移入
  `artifacts/` 对应版本目录后再发布。
- 发布 tag 上的 CI 会校验 `artifacts/` 中便携产物命名版本 == 源码 `__version__`
  （见 `.github/workflows/quality.yml` 的 `release-artifact-version` job），不一致即失败。
