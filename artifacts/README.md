# 构建与发布产物

## 版本来源（必读）

**构建产物的版本号唯一来源是 `src/omnicrawl/__init__.py` 中的 `__version__`。**
`build_windows.ps1` 启动时会自动读取，产物命名由脚本决定，严禁在构建
过程中手动修改版本号。版本号的变更是一个独立操作（通过 `tools/bump_version.py`），
绝对不与构建、修复、测试等其他操作混在一起。

---

`artifacts/` 是 OmniCrawler 的版本化本地交付物归档；它不是源码，也不应被源码 ZIP 再次打包。

```text
artifacts/
  build/<version>-<edition>-rN/release/OmniCrawler/  完整 Windows 便携目录
  release/<version>/                                Windows ZIP 与 SHA-256 清单
  python/<version>/                                 源码 ZIP 与 wheel
  package-source/                                   从源码 ZIP 解压出的 wheel 构建暂存
  tmp/                                              可恢复的短期构建临时文件
```

## 产物种类（完整列表）

每次构建生成 **4 类产物**，全部从同一个 `__version__` 派生命名：

| # | 产物类型 | 示例文件名 | 输出目录 |
|---|---|---|---|
| 1 | **Standard 便携 ZIP** | `OmniCrawler-0.2.0-Windows-Portable-Standard.zip` | `artifacts/release/{version}/` |
| 2 | **Full 便携 ZIP** | `OmniCrawler-0.2.0-Windows-Portable-Full.zip` | `artifacts/release/{version}/` |
| 3 | **源码 ZIP** | `OmniCrawler-0.2.0-Source.zip` | `artifacts/python/{version}/` |
| 4 | **完整便携目录**（压缩前）| `release/OmniCrawler/` | `artifacts/build/{version}-{edition}-rN/` |

- #4 是 #1/#2 的压缩前完整文件夹，可直接双击运行、调试。
- #3 是纯源码归档（不含 `artifacts/`、`build_cache/`、`dist/` 等构建物）。
- wheel（`omnicrawl_platform-0.2.0-py3-none-any.whl`）随 #3 同一目录产出。

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
