# 安装、运行与平台矩阵

> 适用版本：0.13.1 · 配置协议：v5 · 维护状态：现行

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
   `work/` / `data/` / `output/` / `logs/` / `.omnicrawler/` / `PORTABLE.flag` 是**受保护路径**，
   升级包**不允许**触碰它们（`services/updater.py` 的 `PROTECTED_TOP_LEVEL`，越界即整包拒绝）。
2. **应用内自更新（配置了更新源的人）**：

   ```bash
   omnicrawler self-update check -c task.yaml            # 只看：有没有新版本（只读）
   omnicrawler self-update apply -c task.yaml --dry-run  # 立刻返回计划，不写任何文件
   omnicrawler self-update apply -c task.yaml --yes      # 取包 → 核对 sha256 → 验签 → 覆盖（可回滚）
   ```

   需要两项配置（缺任一即视为**已禁用**，退出码 `2`）：

   ```yaml
   self_update:
     feed_url: "https://<你的更新源>/omnicrawler"   # 含 update.json 与各平台资产；也可直接填本地目录
     trusted_public_key: "<base64 的 ed25519 公钥，32 字节>"   # 必填；留空=禁用
     edition: "Standard"                            # Standard | Full
   ```

   ★ **本命令没有「跳过验签」开关**（安全不降级）：`update.json` 必须带 `signature`，
   且签名覆盖除该字段外的全部内容 —— 改任何一个字节都会被拒。
   ★ 退出码四态：`0` 已最新 · `1` 有可用更新 · `2` 未配置信任根或更新源（禁用）· `3` 失败。
   ★ `apply` 覆盖前会把原文件备份到 `<应用根>/.updates/rollback/<时间戳>/`，据此可回退。
   ★ 更新源**不绑定任何托管方**（远程目录或本地目录均可）；本地目录形态可用于内网镜像与离线环境。

## 外部服务并非本机依赖

Redis、S3、PostgreSQL 与 OpenSearch 的 Python 客户端包含在 full 中，但服务端不应
捆绑到桌面应用。只有配置相应后端时才需要独立服务地址、网络与凭据；默认 SQLite、
本地文件、DuckDB 和 Parquet 完全离线。
