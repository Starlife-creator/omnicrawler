#!/usr/bin/env bash
# =============================================================================
# build_macos.sh — macOS 便携包构建
#
# 与 build_linux.sh 同源模式（隔离 venv / 三重版本校验 / 冒烟测试 / 完整性清单），
# 差异：
#   - 使用 OmniCrawler-macOS.spec（BUNDLE 生成 .app，CLI/worker 进 Contents/MacOS）
#   - 对 .app 做 ad-hoc 签名（codesign --sign -），无需开发者证书；
#     首次打开需右键 -> 打开（Gatekeeper 对未公证应用的限制）
#   - 打包为 .dmg（hdiutil），失败时回退 tar.gz
#
# 用法：
#   ./build_macos.sh                       # Standard 便携包
#   ./build_macos.sh --edition Full
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")"

EDITION="Standard"
BUILD_ROOT=""
RELEASE_OUTPUT=""
BROWSER_CACHE_PATH=""
SKIP_BROWSER_DOWNLOAD=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --edition) EDITION="$2"; shift 2 ;;
    --build-root) BUILD_ROOT="$2"; shift 2 ;;
    --release-output) RELEASE_OUTPUT="$2"; shift 2 ;;
    --browser-cache-path) BROWSER_CACHE_PATH="$2"; shift 2 ;;
    --skip-browser-download) SKIP_BROWSER_DOWNLOAD=1; shift ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

case "$EDITION" in
  Standard|Full) ;;
  *) echo "Edition must be Standard or Full, got: $EDITION" >&2; exit 2 ;;
esac

PROJECT_ROOT="$(pwd)"
if [[ -z "$BUILD_ROOT" ]]; then
  BUILD_ROOT="${TMPDIR:-/tmp}/OmniCrawler-build-$(echo "$EDITION" | tr '[:upper:]' '[:lower:]')"
fi
BINARY_ROOT="$BUILD_ROOT/bin"
WORK_ROOT="$BUILD_ROOT/work"
BROWSERS_ROOT="$BUILD_ROOT/browsers"
RELEASE_ROOT="$BUILD_ROOT/release/OmniCrawler"
if [[ -z "$RELEASE_OUTPUT" ]]; then
  RELEASE_OUTPUT="$PROJECT_ROOT/release"
fi
BUILDER_VENV="$BUILD_ROOT/venv"
BUILDER_PYTHON="$BUILDER_VENV/bin/python"
# 按 edition 选择 spec：Full 用不 excludes cv2/selenium 的真 Full spec（macOS 保留 paddle excludes）
if [[ "$EDITION" == "Full" ]]; then
  SPEC_FILE="$PROJECT_ROOT/packaging/OmniCrawler-macOS-Full.spec"
  RUNTIME_ROOT="$BUILD_ROOT/runtime"
else
  SPEC_FILE="$PROJECT_ROOT/packaging/OmniCrawler-macOS.spec"
  RUNTIME_ROOT=""
fi

# ---- 平台断言 ---------------------------------------------------------------
if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "build_macos.sh 只能在 macOS 上运行（PyInstaller 不支持交叉编译）。" >&2; exit 1
fi

# ---- framework Python 选择 --------------------------------------------------
# PyInstaller 在 macOS 打包 .app 必须用 framework 版 Python（--enable-framework），
# 否则 .app/Contents/Frameworks/Python 缺失，运行时报 Failed to load Python shared
# library（PYI-9229/2508/20814）。GitHub Actions setup-python 安装的是非 framework
# build；runner 预装 homebrew python 是 framework build。
# ⚠ 版本必须与 PyInstaller 6.15.0 兼容：homebrew 默认 python3 已是 3.14（不受支持），
# **且 python@3.13 的 framework BUNDLE 有 PYI-5670 问题（.app 缺 Frameworks/Python，
# 见 2026-08-16 v0.9.0/v0.9.1 构建失败）**——故强制用 python@3.12（与
# BUILD_PYTHON_VERSION=3.12 对齐，v0.8.0 已验证可用），不再 fallback 3.13。
FRAMEWORK_PYTHON=""
if [[ -x /opt/homebrew/opt/python@3.12/bin/python3.12 ]]; then
  FRAMEWORK_PYTHON=/opt/homebrew/opt/python@3.12/bin/python3.12
else
  echo "未找到 python@3.12（framework），尝试 brew install python@3.12 ..." >&2
  brew install python@3.12 || { echo "brew install python@3.12 失败" >&2; exit 1; }
  FRAMEWORK_PYTHON=/opt/homebrew/opt/python@3.12/bin/python3.12
fi
# 上界硬断言：PyInstaller 6.15 不支持 3.14+；3.13 因 PYI-5670 也不采用
_py_minor=$("$FRAMEWORK_PYTHON" -c 'import sys; print(sys.version_info[1])' 2>/dev/null)
if [[ "$_py_minor" -ne 12 ]]; then
  echo "framework Python 必须是 3.12，当前：$("$FRAMEWORK_PYTHON" --version 2>&1)" >&2
  exit 1
fi
echo "framework Python: $FRAMEWORK_PYTHON ($("$FRAMEWORK_PYTHON" --version 2>&1))"

# ---- Python 版本断言（与 pyproject requires-python >=3.12 对齐）-----------
if ! "$FRAMEWORK_PYTHON" -c 'import sys; sys.exit(0 if sys.version_info>=(3,12) else 1)' 2>/dev/null; then
  echo "需 Python ≥3.12，当前：$("$FRAMEWORK_PYTHON" --version 2>&1 || echo '未知')" >&2
  exit 1
fi

# ---- 依赖安装（隔离 venv） ---------------------------------------------------
if [[ ! -x "$BUILDER_PYTHON" ]]; then
  "$FRAMEWORK_PYTHON" -m venv "$BUILDER_VENV"
fi
"$BUILDER_PYTHON" -m pip install --upgrade pip setuptools wheel
if [[ "$EDITION" == "Full" ]]; then
  # full-macos：full 去掉 paddleocr/paddlepaddle（macOS 无稳定 wheel）+ 显式 opencv
  EXTRAS="full-macos"
else
  EXTRAS="gui,html,pdf,browser,async-http,security"
fi
"$BUILDER_PYTHON" -m pip install -e "$PROJECT_ROOT[$EXTRAS]" pyinstaller==6.15.0

# ---- 三重版本校验 -----------------------------------------------------------
APP_VERSION="$("$BUILDER_PYTHON" -c 'from omnicrawl import __version__; print(__version__)')"
PYPROJECT_VERSION="$("$BUILDER_PYTHON" -c 'import tomllib; print(tomllib.load(open("pyproject.toml","rb"))["project"]["version"])')"
INSTALLED_VERSION="$("$BUILDER_PYTHON" -c 'import importlib.metadata; print(importlib.metadata.version("omnicrawl-platform"))')"
if [[ -z "$APP_VERSION" || ! "$APP_VERSION" =~ ^[0-9]+\.[0-9]+ ]]; then
  echo "Invalid application version: '$APP_VERSION'" >&2; exit 1
fi
if [[ "$APP_VERSION" != "$PYPROJECT_VERSION" ]]; then
  echo "版本不一致: pyproject=$PYPROJECT_VERSION vs omnicrawl.__version__=$APP_VERSION" >&2; exit 1
fi
if [[ "$INSTALLED_VERSION" != "$APP_VERSION" ]]; then
  echo "版本元数据漂移: installed=$INSTALLED_VERSION vs src=$APP_VERSION —— 需重跑 pip install -e ." >&2; exit 1
fi

echo "============================================================"
echo "  OmniCrawler $APP_VERSION — $EDITION edition macOS portable build"
echo "  Build root : $BUILD_ROOT"
echo "  Release    : $RELEASE_OUTPUT"
echo "============================================================"

# ---- 浏览器运行时 -----------------------------------------------------------
# 三种来源，优先级从高到低：
#   1. --browser-cache-path 缓存复制（CI 命中缓存：缓存目录已有 Chromium）
#   2. playwright 联网下载（CI 未命中缓存：下载到 $BROWSER_DOWNLOAD_ROOT 并回填缓存目录）
#   3. --skip-browser-download 假设已有
# 设计要点：CI 里外部 PLAYWRIGHT_BROWSERS_PATH 指向缓存目录（actions/cache path）。
# 首次构建（未命中）playwright 下载进缓存目录 → 可被 actions/cache save 保存；
# 命中缓存时经 --browser-cache-path 复制到构建目录。脚本总是把浏览器归一到
# $BROWSERS_ROOT 供 PyInstaller 组装。
BROWSER_DOWNLOAD_ROOT="${PLAYWRIGHT_BROWSERS_PATH:-$BROWSERS_ROOT}"
if [[ -n "$BROWSER_CACHE_PATH" ]]; then
  if [[ ! -d "$BROWSER_CACHE_PATH" ]]; then
    echo "Browser cache path not found: $BROWSER_CACHE_PATH" >&2; exit 1
  fi
  rm -rf "$BROWSERS_ROOT"
  mkdir -p "$BROWSERS_ROOT"
  cp -r "$BROWSER_CACHE_PATH/." "$BROWSERS_ROOT/"
  echo "[INFO] Copied Chromium from cache: $BROWSER_CACHE_PATH"
elif [[ "$SKIP_BROWSER_DOWNLOAD" -eq 0 ]]; then
  export PLAYWRIGHT_BROWSERS_PATH="$BROWSER_DOWNLOAD_ROOT"
  "$BUILDER_PYTHON" -m playwright install chromium
  if [[ "$BROWSER_DOWNLOAD_ROOT" != "$BROWSERS_ROOT" ]]; then
    rm -rf "$BROWSERS_ROOT"
    mkdir -p "$BROWSERS_ROOT"
    cp -r "$BROWSER_DOWNLOAD_ROOT/." "$BROWSERS_ROOT/"
  fi
else
  echo "[INFO] --skip-browser-download：假设 $BROWSERS_ROOT 已有可用 Chromium"
fi
if [[ ! -d "$BROWSERS_ROOT" ]]; then
  echo "Bundled Chromium not found: $BROWSERS_ROOT" >&2; exit 1
fi

# ---- OCR 运行时制备（仅 Full：Tesseract/ChromeDriver，不制备 Paddle）---------
if [[ "$EDITION" == "Full" ]]; then
  echo "[Full] 制备 OCR 运行时 -> $RUNTIME_ROOT"
  bash "$PROJECT_ROOT/packaging/prepare_macos_runtime.sh" \
    --python "$BUILDER_PYTHON" --runtime-root "$RUNTIME_ROOT" --browsers-root "$BROWSERS_ROOT"
fi

# ---- PyInstaller 构建（.app bundle） -----------------------------------------
rm -rf "$BINARY_ROOT" "$WORK_ROOT"
mkdir -p "$BINARY_ROOT" "$WORK_ROOT"
"$BUILDER_PYTHON" -m PyInstaller --noconfirm --clean \
  --distpath "$BINARY_ROOT" --workpath "$WORK_ROOT" "$SPEC_FILE"

APP_BUNDLE="$BINARY_ROOT/OmniCrawler.app"
if [[ ! -d "$APP_BUNDLE" ]]; then
  echo "PyInstaller .app bundle not found: $APP_BUNDLE" >&2; exit 1
fi
for required in Contents/MacOS/OmniCrawler Contents/MacOS/omnicrawl Contents/MacOS/omnicrawl-worker; do
  if [[ ! -e "$APP_BUNDLE/$required" ]]; then
    echo "PyInstaller output is incomplete: $required" >&2; exit 1
  fi
done

# Full：把 OCR 运行时拷进 .app/Contents/MacOS/runtime（= application_dir()/runtime，
# 运行时经 configure_runtime_environment 自动探测；在 ad-hoc 签名之前拷入，
# 使整棵 dylib 树被下方 codesign --deep 统一重签）。
if [[ "$EDITION" == "Full" && -d "$RUNTIME_ROOT" ]]; then
  mkdir -p "$APP_BUNDLE/Contents/MacOS/runtime"
  cp -R "$RUNTIME_ROOT/." "$APP_BUNDLE/Contents/MacOS/runtime/"
fi

# ---- ad-hoc 签名（无需开发者证书） -------------------------------------------
echo "codesign (ad-hoc): $APP_BUNDLE"
codesign --force --deep --sign - "$APP_BUNDLE"
codesign --verify --deep --strict "$APP_BUNDLE" \
  && echo "codesign verify OK" || { echo "codesign verify 失败" >&2; exit 1; }

# ---- 组装暂存目录（.app + 文档 + 运行时） ------------------------------------
rm -rf "$BUILD_ROOT/release"
mkdir -p "$RELEASE_ROOT"
cp -R "$APP_BUNDLE" "$RELEASE_ROOT/"
cp -R "$BROWSERS_ROOT" "$RELEASE_ROOT/browsers"
cp "$PROJECT_ROOT/README.md" "$PROJECT_ROOT/LICENSE" "$PROJECT_ROOT/packaging/THIRD_PARTY_NOTICES.md" "$RELEASE_ROOT/"
echo "OmniCrawler $EDITION portable edition" > "$RELEASE_ROOT/EDITION.txt"
for directory in configs docs examples; do
  cp -R "$PROJECT_ROOT/$directory" "$RELEASE_ROOT/"
done
touch "$RELEASE_ROOT/PORTABLE.flag"
for relative_dir in data/input data/pdfs work output logs; do
  mkdir -p "$RELEASE_ROOT/$relative_dir"
done

# ---- 产物级测试（SBOM + CLI 冒烟 + portable 冒烟 + 完整性清单）--------------
# 与 Windows 构建对齐：落盘 CAPABILITIES.json / RELEASE-INFO.json 并重刷清单
"$BUILDER_PYTHON" "$PROJECT_ROOT/tools/generate_sbom.py" --output "$RELEASE_ROOT/SBOM.json"
"$RELEASE_ROOT/OmniCrawler.app/Contents/MacOS/omnicrawl" --version
"$RELEASE_ROOT/OmniCrawler.app/Contents/MacOS/omnicrawl" templates validate
"$RELEASE_ROOT/OmniCrawler.app/Contents/MacOS/omnicrawl" capabilities --verify-imports --portable-paths > "$RELEASE_ROOT/CAPABILITIES.json"
"$BUILDER_PYTHON" "$PROJECT_ROOT/tools/generate_release_info.py" \
    --project-root "$PROJECT_ROOT" --release-root "$RELEASE_ROOT" --edition "$EDITION"
# P4-3：portable 冒烟（浏览器/原生运行时）。其 cwd=releaseRoot 会写缓存，
# 必须在完整性清单生成之前运行（与 Windows F11 同约束）。
"$BUILDER_PYTHON" "$PROJECT_ROOT/tools/portable_smoke_test.py" "$RELEASE_ROOT" --edition "$EDITION"

# M6（Full）：产物内 OCR 运行时可调用冒烟（Tesseract/ChromeDriver；Paddle 不打包）
if [[ "$EDITION" == "Full" ]]; then
  echo "[Full] OCR 运行时冒烟..."
  "$RELEASE_ROOT/OmniCrawler.app/Contents/MacOS/runtime/tesseract/tesseract" \
    --tessdata-dir "$RELEASE_ROOT/OmniCrawler.app/Contents/MacOS/runtime/tesseract/tessdata" \
    --list-langs >/dev/null 2>&1 || { echo "产物内 Tesseract 不可用" >&2; exit 1; }
  "$RELEASE_ROOT/OmniCrawler.app/Contents/MacOS/runtime/selenium/chromedriver" --version >/dev/null 2>&1 \
    || { echo "产物内 ChromeDriver 不可用" >&2; exit 1; }
  echo "[Full] OCR 运行时冒烟通过"
fi

# 完整性清单：在新增 CAPABILITIES.json / RELEASE-INFO.json 之后生成，
# 使清单覆盖这两个机器可读文件（与 Windows 同序：先加文件再刷清单）。
"$BUILDER_PYTHON" "$PROJECT_ROOT/tools/create_runtime_manifest.py" --release-root "$RELEASE_ROOT"
"$RELEASE_ROOT/OmniCrawler.app/Contents/MacOS/omnicrawl" runtime-verify --root "$RELEASE_ROOT"
# P4-1：Windows 对 zip 跑 check_release_integrity --portable-zip --portable-deep；
# macOS dmg/tar.gz 暂不做同等深校验（工具仅支持 zipfile 容器，扩展为多格式是后续项）。
# 结构对称性由上方 runtime-verify（RUNTIME-MANIFEST 双向核对）+ CLI 冒烟兜底。

# ---- 打包 dmg（失败回退 tar.gz） ----------------------------------------------
mkdir -p "$RELEASE_OUTPUT"
DMG_ARCHIVE="$RELEASE_OUTPUT/OmniCrawler-$APP_VERSION-macOS-Portable-$EDITION.dmg"
# -srcfolder 指向父目录，使 dmg 卷内含 OmniCrawler/ 顶层文件夹（与 Windows zip /
# Linux tar.gz 的顶层 OmniCrawler/ 对齐）；tar.gz 回退同理。
if hdiutil create -volname "OmniCrawler" -srcfolder "$BUILD_ROOT/release" \
    -ov -format UDZO "$DMG_ARCHIVE" >/dev/null 2>&1; then
  echo "Portable archive: $DMG_ARCHIVE"
else
  echo "[WARN] hdiutil 失败，回退 tar.gz"
  TAR_ARCHIVE="$RELEASE_OUTPUT/OmniCrawler-$APP_VERSION-macOS-Portable-$EDITION.tar.gz"
  tar -czf "$TAR_ARCHIVE" -C "$BUILD_ROOT/release" OmniCrawler
  echo "Portable archive: $TAR_ARCHIVE"
fi

echo "Build staging: $RELEASE_ROOT"
echo "CLI: $RELEASE_ROOT/OmniCrawler.app/Contents/MacOS/omnicrawl --help"
