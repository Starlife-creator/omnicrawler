# 安装、运行与平台矩阵

## 推荐路径

| 使用者 | 推荐方式 | 是否需要系统 Python | 默认能力 |
|---|---|---:|---|
| Windows 普通用户 | 全量便携 ZIP | 否 | 全部本地功能与客户端依赖 |
| Windows 开发者 | `setup_windows.bat` | 是 | `full + dev` |
| Linux 开发者/服务器 | `./setup_linux.sh` | 是 | `full + dev` |
| macOS 开发者 | `./setup_macos.command` | 是 | `full + dev` |
| 容器/定制服务 | Docker/选择性 extras | 否 | 按镜像用途裁剪 |

## Windows 源码模式

```powershell
.\setup_windows.bat
.\run_gui_windows.bat
.\.venv\Scripts\omnicrawl.exe capabilities --verify-imports
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
python -m omnicrawl --help
python -m omnicrawl capabilities --verify-imports
python -m omnicrawl.gui
python -m omnicrawl.pdfx --help
```

所有平台使用同一 `src/omnicrawl` 核心、配置格式、插件 API、测试和文档。平台脚本
只负责解释器路径、原生依赖与启动体验，不复制业务实现。

## 外部服务并非本机依赖

Redis、S3、PostgreSQL 与 OpenSearch 的 Python 客户端包含在 full 中，但服务端不应
捆绑到桌面应用。只有配置相应后端时才需要独立服务地址、网络与凭据；默认 SQLite、
本地文件、DuckDB 和 Parquet 完全离线。
