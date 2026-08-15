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
RELEASE_ROOT="$BUILD_ROOT/release"
if [[ -z "$RELEASE_OUTPUT" ]]; then
  RELEASE_OUTPUT="$PROJECT_ROOT/release"
fi
BUILDER_VENV="$BUILD_ROOT/venv"
BUILDER_PYTHON="$BUILDER_VENV/bin/python"
SPEC_FILE="$PROJECT_ROOT/packaging/OmniCrawler-macOS.spec"

# ---- 平台断言 ---------------------------------------------------------------
if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "build_macos.sh 只能在 macOS 上运行（PyInstaller 不支持交叉编译）。" >&2; exit 1
fi

# ---- framework Python 选择 --------------------------------------------------
# PyInstaller 在 macOS 打包 .app 必须用 framework 版 Python（--enable-framework），
# 否则 .app/Contents/Frameworks/Python 缺失，运行时报 Failed to load Python shared
# library（PYI-9229/2508/20814）。GitHub Actions setup-python 安装的是非 framework
# build；runner 预装的 homebrew python 是 framework build。
if [[ -x /opt/homebrew/bin/python3 ]]; then
  FRAMEWORK_PYTHON=/opt/homebrew/bin/python3
elif [[ -x /usr/local/bin/python3 ]]; then
  FRAMEWORK_PYTHON=/usr/local/bin/python3
else
  echo "未找到 framework 版 Python（/opt/homebrew 或 /usr/local 的 python3）" >&2; exit 1
fi
echo "framework Python: $FRAMEWORK_PYTHON ($("$FRAMEWORK_PYTHON" --version 2>&1))"

# ---- 依赖安装（隔离 venv） ---------------------------------------------------
if [[ ! -x "$BUILDER_PYTHON" ]]; then
  "$FRAMEWORK_PYTHON" -m venv "$BUILDER_VENV"
fi
"$BUILDER_PYTHON" -m pip install --upgrade pip setuptools wheel
if [[ "$EDITION" == "Full" ]]; then
  EXTRAS="full"
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

# ---- ad-hoc 签名（无需开发者证书） -------------------------------------------
echo "codesign (ad-hoc): $APP_BUNDLE"
codesign --force --deep --sign - "$APP_BUNDLE"
codesign --verify --deep --strict "$APP_BUNDLE" \
  && echo "codesign verify OK" || { echo "codesign verify 失败" >&2; exit 1; }

# ---- 组装暂存目录（.app + 文档 + 运行时） ------------------------------------
rm -rf "$RELEASE_ROOT"
mkdir -p "$RELEASE_ROOT"
cp -R "$APP_BUNDLE" "$RELEASE_ROOT/"
cp -R "$BROWSERS_ROOT" "$RELEASE_ROOT/browsers"
cp "$PROJECT_ROOT/README.md" "$PROJECT_ROOT/LICENSE" "$RELEASE_ROOT/"
echo "OmniCrawler $EDITION portable edition" > "$RELEASE_ROOT/EDITION.txt"
for directory in configs docs examples; do
  cp -R "$PROJECT_ROOT/$directory" "$RELEASE_ROOT/"
done
touch "$RELEASE_ROOT/PORTABLE.flag"
for relative_dir in data/input data/pdfs work output logs; do
  mkdir -p "$RELEASE_ROOT/$relative_dir"
done

# ---- 产物级测试（SBOM + CLI 冒烟 + portable 冒烟 + 完整性清单）--------------
# P4-2：与 Windows 对齐，SBOM 写入包内
"$BUILDER_PYTHON" "$PROJECT_ROOT/tools/generate_sbom.py" --output "$RELEASE_ROOT/SBOM.json"
"$RELEASE_ROOT/OmniCrawler.app/Contents/MacOS/omnicrawl" --version
"$RELEASE_ROOT/OmniCrawler.app/Contents/MacOS/omnicrawl" templates validate
"$RELEASE_ROOT/OmniCrawler.app/Contents/MacOS/omnicrawl" capabilities --verify-imports --portable-paths
# P4-3：portable 冒烟（浏览器/原生运行时）。其 cwd=releaseRoot 会写缓存，
# 必须在完整性清单生成之前运行（与 Windows F11 同约束）。
"$BUILDER_PYTHON" "$PROJECT_ROOT/tools/portable_smoke_test.py" "$RELEASE_ROOT" --edition "$EDITION"

"$BUILDER_PYTHON" "$PROJECT_ROOT/tools/create_runtime_manifest.py" --release-root "$RELEASE_ROOT"
"$RELEASE_ROOT/OmniCrawler.app/Contents/MacOS/omnicrawl" runtime-verify --root "$RELEASE_ROOT"
# P4-1：Windows 对 zip 跑 check_release_integrity --portable-zip --portable-deep；
# macOS dmg/tar.gz 暂不做同等深校验（工具仅支持 zipfile 容器，扩展为多格式是后续项）。
# 结构对称性由上方 runtime-verify（RUNTIME-MANIFEST 双向核对）+ CLI 冒烟兜底。

# ---- 打包 dmg（失败回退 tar.gz） ----------------------------------------------
mkdir -p "$RELEASE_OUTPUT"
DMG_ARCHIVE="$RELEASE_OUTPUT/OmniCrawler-$APP_VERSION-macOS-Portable-$EDITION.dmg"
if hdiutil create -volname "OmniCrawler" -srcfolder "$RELEASE_ROOT" \
    -ov -format UDZO "$DMG_ARCHIVE" >/dev/null 2>&1; then
  echo "Portable archive: $DMG_ARCHIVE"
else
  echo "[WARN] hdiutil 失败，回退 tar.gz"
  TAR_ARCHIVE="$RELEASE_OUTPUT/OmniCrawler-$APP_VERSION-macOS-Portable-$EDITION.tar.gz"
  tar -czf "$TAR_ARCHIVE" -C "$RELEASE_ROOT" .
  echo "Portable archive: $TAR_ARCHIVE"
fi

echo "Build staging: $RELEASE_ROOT"
echo "CLI: $RELEASE_ROOT/OmniCrawler.app/Contents/MacOS/omnicrawl --help"
