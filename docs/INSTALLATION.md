# 安装、运行与平台矩阵

> 适用版本：0.15.0 · 配置协议：v5 · 维护状态：现行（历史状态见归档；功能验证以验收账本为准）

## 推荐路径

| 使用者 | 推荐方式 | 是否需要系统 Python | 默认能力 |
|---|---|---:|---|
| Windows 普通用户 | 全量便携 ZIP | 否 | 全部本地功能与客户端依赖 |
| Windows 开发者 | `setup_windows.bat` | 是 | `full + dev` |
| Linux 普通用户 | 便携 tar.xz（可选 `installer/install-user.sh` 装进应用菜单） | 否 | 全部本地功能与客户端依赖 |
| Linux 开发者/服务器 | `./setup_linux.sh` | 是 | `full + dev` |
| macOS 开发者 | `./setup_macos.command` | 是 | `full + dev` |
| 容器/定制服务 | Docker/选择性 extras | 否 | 按镜像用途裁剪 |

## Windows 源码模式

```powershell
.\setup_windows.bat
.\run_gui_windows.bat
.\.venv\Scripts\omnicrawler.exe capabilities --verify-imports
```

默认下载完整 Python 依赖、Chromium 与 PaddleOCR 模型，并准备项目本地 Windows
原生运行时。`-Minimal` 仅供明确需要定制精简环境的开发者。

## Linux

```bash
chmod +x setup_linux.sh run_gui_linux.sh run_linux.sh
./setup_linux.sh
./run_gui_linux.sh
./run_linux.sh capabilities --verify-imports
```

GUI 需要系统 Qt/X11/Wayland 库。Tesseract 建议通过发行版安装
`tesseract-ocr tesseract-ocr-eng tesseract-ocr-chi-sim`；PaddleOCR 是完整本地后备。
服务器无桌面时使用 CLI，不需要启动 GUI。

不想装 Python 的普通用户走便携 tar.xz：解压即用；若还想让它出现在应用菜单里
（并带图标、可干净卸载），包内的 `./installer/install-user.sh` 会做一次**用户级**
安装（不需要 root）—— 详见 [Linux 用户级安装](LINUX_INSTALL.md)。

## macOS

```bash
chmod +x setup_macos.command run_gui_macos.command run_macos.sh
./setup_macos.command
./run_gui_macos.command
```

Intel 与 Apple Silicon 均使用当前解释器对应的原生 wheel。Tesseract 可由 Homebrew
安装；PaddleOCR 支持情况以当前 wheel 为准，安装脚本会明确报告而不会静默跳过。

## 通用 Python 入口

```bash
python -m omnicrawler --help
python -m omnicrawler capabilities --verify-imports
python -m omnicrawler.gui
python -m omnicrawler.pdfx --help
```

所有平台使用同一 `src/omnicrawler` 核心、配置格式、插件 API、测试和文档。平台脚本
只负责解释器路径、原生依赖与启动体验，不复制业务实现。

## 升级到新版本（两条路径，按人群选）

本仓库**不做 MSI/pkg/deb 安装器**（见《优化方案》§6.3），升级因此有两条路，适用人群不同：

1. **手动替换便携包（所有人，零前提）**：下载新版本便携包解压覆盖程序文件即可。
   `work/` / `data/` / `output/` / `logs/` / `.omnicrawler/` / `plugins_installed/` / `PORTABLE.flag`
   是**受保护路径**，升级包**不允许**触碰它们（`services/updater.py` 的 `PROTECTED_TOP_LEVEL`，越界即整包拒绝）。
   ★ 注意 `configs/` **不在**受保护名单里：它是随包目录（含内置信任根 `plugin_trust.pub.pem`），
   必须能被升级更新，否则信任根永远无法随升级轮换 —— 所以那里只放随包文件，别把自己的配置放进去。
2. **应用内自更新（配置了更新源的人）**：

   ```bash
   omnicrawler self-update check -c task.yaml            # 只看：有没有新版本（只读）
   omnicrawler self-update apply -c task.yaml --dry-run  # 立刻返回计划，不写任何文件
   omnicrawler self-update apply -c task.yaml --yes      # 取包 → 核对 sha256 → 验签 → 覆盖（可回滚）
   ```

   需要两项配置（缺任一即视为**已禁用**，退出码 `2`）：

   ```yaml
   self_update:
     feed_url: ""                                   # 留空 = 默认更新源（GitHub 官方发布页，见下）
     trusted_public_key: ""                         # 留空 = 用随包内置信任根 configs/update_trust.pub.pem
     edition: "Standard"                            # Standard | Full
   ```

   ★ **默认更新源**＝`https://github.com/Starlife-creator/omnicrawler/releases/latest/download`
   （GitHub「最新 release 资产」固定链接：每版把 `update.json` 当普通 Release 资产上传，
   客户端永远拿到最新清单——免版本号、免 API、零额外托管）。显式配置可指向镜像/本地目录；
   **清单带 sha256 ⇒ 镜像不必可信**（只承担带宽）。

   ★ **信任根随包内置**（`configs/update_trust.pub.pem`），因此通常**只需要填 `feed_url`**；
   显式填 `trusted_public_key` 会覆盖内置值（自建源/轮换时用）。**内置文件缺失即视为禁用**——
   不存在"跳过验签"的降级路径。
   ★ **更新源可以只发变化的那部分**：若 `update.json` 带 `payload.files`（逐文件清单）与
   `payload.delta`（变更包），`check` 会先比对**本机每个文件的实际哈希**，只报"需更新几个文件、
   共多少字节"，`apply` 也只取这些 —— 515MB / 1.9GB 的便携包，常规版本升级通常只需几 MB。
   清单里有、本机没有的文件计入"需下载"；`payload.deleted` 列出本版**已移除**的路径，
   本机若还残留会被显式列出（不会被静默留着）。
   ★ 维护者生成本文件：`python tools/build_update_manifest.py --payload-dir <解压后的载荷> --version X.Y.Z
   --asset <平台-版本>=<整包> [--delta <旧版本>=<变更包>] [--previous-payload-dir <上一版载荷>]
   --key <ed25519 冷私钥路径> --out update.json`（冷密钥只读入内存，绝不打印/入仓）。

   ★ **本命令没有「跳过验签」开关**（安全不降级）：`update.json` 必须带 `signature`，
   且签名覆盖除该字段外的全部内容 —— 改任何一个字节都会被拒。
   ★ 退出码四态：`0` 已最新 · `1` 有可用更新 · `2` 未配置信任根或更新源（禁用）· `3` 失败。
   ★ `apply` 是**就地替换**，且**不复制备份副本**：先把旧文件改名让位（`qt.dll` → `qt.dll.old-<随机>`），
   再把新内容写到原名上，最后删掉那个改名出来的旧文件 —— 磁盘上始终只有一份。
   ★ 例外：若某文件**正在被占用**（如运行中的 DLL/EXE），那一份会**当场删不掉** ⇒ 登记进
   `<应用根>/.updates/pending-cleanup.json`，**下次启动时自动清掉**（`--dry-run` 不清理、不写盘）。
   ★ 失败可回滚：把新文件删掉、把 `.old-*` 改名回去（同卷改名，瞬时、零额外空间）。
   ★ 被替换之外的文件**一个都不动**（未变的重依赖连修改时间都不变），所以常规增量只下几 MB。
   ★ **三种落地方式**（`check` 会把它们需要的体积并排报出来，照 B 站弹窗的形态）：

   | 方式 | 命令 | 磁盘 | 适用 |
   |---|---|---|---|
   | **增量更新** | `self-update apply -c task.yaml --yes` | **一份** | 有"相对当前版本的变更包"时（默认）。发布侧为**最近 3 个版本**各出一个变更包，所以跨两三版升级也能走增量 |
   | **全量·就地替换** | `self-update apply -c task.yaml --full --yes` | **一份** | 显式选全量；或本机版本不在增量窗口内（自动兜底） |
   | **全量·装到 versions/** | `self-update apply -c task.yaml --full --to-versions --yes` | **两份** | 想保留当前版本以便回退（就地那份原样不动）。三平台都可用：Windows 靠 `OmniCrawler-Launcher.bat`，Linux/macOS 靠随包发的 `OmniCrawler-launcher` / `omnicrawler-cli-launcher` —— 三者读**同一份** `versions/current.txt` |
   | **手动安装**（macOS） | 见下 | — | macOS **不支持自动更新**：只提示有新版并给出要下载的文件名与体积 |

   ★ 装到 `versions/<新版>/` 时会写 `versions/current.txt`（入口优先读它指向的版本），
   并把就地布局的**旧版本二进制**改名 `.outdated`（防止误点旧版又触发一次更新）。
   ★ **启动器自己不会被改名** —— 它是版本无关的入口，藏起来就没法启动应用了。
   应用根里那份**原样保留**，你随时可以删，或以后提供"清理旧版本"入口。
   ★ 用户的 `work/`、`data/` 等数据**始终在安装根**（不随版本目录走）；`self-update cleanup`
   也不会删含用户数据的版本目录（会如实报出来但不删）。
   ★ **macOS 只能手动安装**：主产物是 `.dmg`（纯 Python 读不了内部）、Chromium 在 `.app`
   之外、ad-hoc 签名会被改坏 ⇒ 自动落地做不到**完整替换**。所以 macOS 上产品只告诉你
   "有新版本 + 该下载哪个文件"，不提供一个点了必然失败的动作。
   ★ **忽略此版本的更新**：`self-update ignore -c task.yaml`（记录当前更新源给出的版本，
   之后同一版本不再提示；出现更新的版本自动恢复）· `self-update ignore -c task.yaml --clear` 恢复。
   ★ 更新源**不绑定任何托管方**（远程目录或本地目录均可）；本地目录形态可用于内网镜像与离线环境。

## 外部服务并非本机依赖

Redis、S3、PostgreSQL 与 OpenSearch 的 Python 客户端包含在 full 中，但服务端不应
捆绑到桌面应用。只有配置相应后端时才需要独立服务地址、网络与凭据；默认 SQLite、
本地文件、DuckDB 和 Parquet 完全离线。
