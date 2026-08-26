#!/usr/bin/env bash
# =============================================================================
# build_linux.sh — Linux 便携包构建
#
# 与 build_windows.ps1 对齐的质量模式（隔离 venv / 三重版本校验 / 冒烟测试 /
# 完整性清单），但为 Linux 独立实现，不依赖 Windows 专属工具链。
#
# 用法：
#   ./build_linux.sh                     # Standard 便携包（GUI+CLI+Chromium）
#   ./build_linux.sh --edition Full      # Full（含 OCR 依赖，系统包 + Paddle 模型）
#
# 产物：
#   OmniCrawler-<version>-Linux-Portable-<Edition>.tar.xz
# 暂存目录（未压缩完整包）：
#   <BuildRoot>/release/OmniCrawler/
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

# ---- Python 版本断言（与 pyproject requires-python >=3.12 对齐）-----------
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info>=(3,12) else 1)' 2>/dev/null; then
  echo "需 Python ≥3.12，当前：$(python3 --version 2>&1 || echo '未找到 python3')" >&2
  exit 1
fi

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
# 按 edition 选择 spec：Full 用不 excludes 重型包的真 Full spec（含 paddle/selenium）
if [[ "$EDITION" == "Full" ]]; then
  SPEC_FILE="$PROJECT_ROOT/packaging/OmniCrawler-Linux-Full.spec"
  RUNTIME_ROOT="$BUILD_ROOT/runtime"
else
  SPEC_FILE="$PROJECT_ROOT/packaging/OmniCrawler-Linux.spec"
  RUNTIME_ROOT=""
fi

# ---- 架构断言：便携包仅面向 64 位 x86_64 / aarch64 -------------------------
ARCH="$(uname -m)"
case "$ARCH" in
  x86_64|aarch64) ;;
  *) echo "OmniCrawler Linux 便携包仅支持 x86_64 / aarch64，检测到 $ARCH" >&2; exit 1 ;;
esac

# ---- 依赖安装（隔离 venv） ---------------------------------------------------
if [[ ! -x "$BUILDER_PYTHON" ]]; then
  python3 -m venv "$BUILDER_VENV"
fi
"$BUILDER_PYTHON" -m pip install --upgrade pip setuptools wheel
if [[ "$EDITION" == "Full" ]]; then
  EXTRAS="full"
else
  EXTRAS="gui,html,pdf,browser,async-http,security"
fi
"$BUILDER_PYTHON" -m pip install -e "$PROJECT_ROOT[$EXTRAS]" pyinstaller==6.15.0

# ---- 三重版本校验（src __version__ == pyproject == installed） -------------
APP_VERSION="$("$BUILDER_PYTHON" -c 'from omnicrawler import __version__; print(__version__)')"
PYPROJECT_VERSION="$("$BUILDER_PYTHON" -c 'import tomllib; print(tomllib.load(open("pyproject.toml","rb"))["project"]["version"])')"
INSTALLED_VERSION="$("$BUILDER_PYTHON" -c 'import importlib.metadata; print(importlib.metadata.version("omnicrawler-platform"))')"
if [[ -z "$APP_VERSION" || ! "$APP_VERSION" =~ ^[0-9]+\.[0-9]+ ]]; then
  echo "Invalid application version: '$APP_VERSION'" >&2; exit 1
fi
if [[ "$APP_VERSION" != "$PYPROJECT_VERSION" ]]; then
  echo "版本不一致: pyproject=$PYPROJECT_VERSION vs omnicrawler.__version__=$APP_VERSION" >&2; exit 1
fi
if [[ "$INSTALLED_VERSION" != "$APP_VERSION" ]]; then
  echo "版本元数据漂移: installed=$INSTALLED_VERSION vs src=$APP_VERSION —— 需重跑 pip install -e ." >&2; exit 1
fi

echo "============================================================"
echo "  OmniCrawler $APP_VERSION — $EDITION edition Linux portable build"
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

# ---- OCR 运行时制备（仅 Full：Tesseract/ChromeDriver/Paddle 模型）-----------
if [[ "$EDITION" == "Full" ]]; then
  echo "[Full] 制备 OCR 运行时 -> $RUNTIME_ROOT"
  bash "$PROJECT_ROOT/packaging/prepare_linux_runtime.sh" \
    --python "$BUILDER_PYTHON" --runtime-root "$RUNTIME_ROOT" --browsers-root "$BROWSERS_ROOT"
fi

# ---- PyInstaller 构建 --------------------------------------------------------
rm -rf "$BINARY_ROOT" "$WORK_ROOT"
mkdir -p "$BINARY_ROOT" "$WORK_ROOT"
"$BUILDER_PYTHON" -m PyInstaller --noconfirm --clean \
  --distpath "$BINARY_ROOT" --workpath "$WORK_ROOT" "$SPEC_FILE"

BUILT_FOLDER="$BINARY_ROOT/OmniCrawler"
for required in OmniCrawler omnicrawler omnicrawler-worker _internal; do
  if [[ ! -e "$BUILT_FOLDER/$required" ]]; then
    echo "PyInstaller output is incomplete: $required" >&2; exit 1
  fi
done

# ---- 组装暂存目录 ------------------------------------------------------------
rm -rf "$RELEASE_ROOT"
mkdir -p "$RELEASE_ROOT"
# -L：PyInstaller 6.15 Linux onedir 的 _internal/ 库是 symlink（指向构建 venv），
# 原样复制时 create_runtime_manifest 的 resolve() 把它们解析到 venv 外部而跳过，
# runtime-verify 报 unknown。dereference 后 portable 包自包含真实文件。
cp -rL "$BUILT_FOLDER/." "$RELEASE_ROOT/"
cp -r "$BROWSERS_ROOT" "$RELEASE_ROOT/browsers"
# Full：把制备的 OCR 运行时拷进包内（application_dir()/runtime，运行时自动探测）
# -L：制备脚本（prepare_linux_runtime.sh）为让 tesseract 的 DT_NEEDED 短名命中，
# 在暂存目录保留了「短名 symlink → 版本号真身」成对结构。但 portable 包必须
# 自包含、不得含 symlink（深校验门禁；Windows 解压/部分 tar 工具也不友好），
# 与上方 _internal 的 cp -rL 同理：解引用后短名成为真实文件副本，DT_NEEDED
# 按名查找依然命中，RPATH $ORIGIN 不受影响。run 32257651374 实测：56 个
# runtime/tesseract/*.so* symlink 原样进 tar → 深校验 symlink/size/unreadable 报错。
if [[ "$EDITION" == "Full" && -d "$RUNTIME_ROOT" ]]; then
  cp -rL "$RUNTIME_ROOT" "$RELEASE_ROOT/runtime"
fi

# Full：Paddle 的 .so 依赖解析修复——Linux 动态加载器不自动搜索
# _internal/paddle/libs/，libpaddle.so 需靠 RPATH 找兄弟库（libmklml_intel.so
# 等）。PyInstaller 收集时保留 wheel 原 RPATH，实际目标目录已变，导致
# capabilities --self-test / runtime import paddle 报
# 'libmklml_intel.so: cannot open shared object file'（v0.9.1 Linux CI 实测）。
# 与 prepare_linux_runtime.sh 对 Tesseract 的处理对齐：patchelf 统一指 rpath
# 到 $ORIGIN（相对库自身所在目录），保证 portable 包自包含、不依赖系统环境。
if [[ "$EDITION" == "Full" ]]; then
  echo "[Full] 修正 Paddle 共享库 RPATH（patchelf --set-rpath \$ORIGIN）..."
  if command -v patchelf >/dev/null 2>&1; then
    _paddle_libs_dir="$RELEASE_ROOT/_internal/paddle/libs"
    if [[ -d "$_paddle_libs_dir" ]]; then
      find "$_paddle_libs_dir" -maxdepth 1 -name '*.so*' -print0 2>/dev/null |
        while IFS= read -r -d '' so; do
          patchelf --set-rpath '$ORIGIN' "$so" 2>/dev/null || true
        done
      echo "[Full] Paddle RPATH 修正完成：$(find "$_paddle_libs_dir" -maxdepth 1 -name '*.so*' | wc -l) 个共享库"
    else
      echo "警告: 产物内未找到 _internal/paddle/libs，Paddle RPATH 修正跳过" >&2
    fi
  else
    echo "警告: 未找到 patchelf，跳过 Paddle RPATH 修正（产物内 import paddle 可能失败）" >&2
  fi

  # L8：Paddle 裸 dlopen 可达性改由运行时 FLAGS_mklml_dir/FLAGS_lapack_dir
  # 环境变量指路（见 src/omnicrawler/core/runtime_paths.py 的
  # configure_runtime_environment）——paddle 的 PHI_DEFINE_EXPORTED_string
  # flag 支持环境变量覆盖（gflags flagsFromEnv），import paddle 时按绝对
  # 路径 dlopen，无需复制 .so。此前"复制 paddle/libs/*.so 到 _internal 根"
  # 虽让库加载成功，却把 Linux Full 包推过 GitHub Release 单文件 2GB
  # （2147483648 字节）上限（历史事故，run 32290393108：Linux Full 归档
  # 实测 2.1G，发布 API 拒绝 size must be less than 2147483648），
  # 故移除复制逻辑。版本细节以 run ID 回溯为准，避免注释内嵌版本号
  # 触发 bump_version 硬编码扫描 WARN。
fi
cp "$PROJECT_ROOT/README.md" "$PROJECT_ROOT/LICENSE" "$PROJECT_ROOT/packaging/THIRD_PARTY_NOTICES.md" "$RELEASE_ROOT/"
echo "OmniCrawler $EDITION portable edition" > "$RELEASE_ROOT/EDITION.txt"
for directory in configs docs examples; do
  cp -r "$PROJECT_ROOT/$directory" "$RELEASE_ROOT/"
done
touch "$RELEASE_ROOT/PORTABLE.flag"
for relative_dir in data/input data/pdfs work output logs; do
  mkdir -p "$RELEASE_ROOT/$relative_dir"
done

# ---- 产物级测试（SBOM + CLI 冒烟 + portable 冒烟 + 完整性清单）--------------
# 与 Windows 构建对齐：落盘 CAPABILITIES.json / RELEASE-INFO.json 并重刷清单
"$BUILDER_PYTHON" "$PROJECT_ROOT/tools/generate_sbom.py" --output "$RELEASE_ROOT/SBOM.json"
"$RELEASE_ROOT/omnicrawler" --version
"$RELEASE_ROOT/omnicrawler" templates validate
"$RELEASE_ROOT/omnicrawler" capabilities --verify-imports --portable-paths > "$RELEASE_ROOT/CAPABILITIES.json"
"$BUILDER_PYTHON" "$PROJECT_ROOT/tools/generate_release_info.py" \
    --project-root "$PROJECT_ROOT" --release-root "$RELEASE_ROOT" --edition "$EDITION"
# P4-3：portable 冒烟（浏览器/原生运行时）。其 cwd=releaseRoot 会写缓存，
# 必须在完整性清单生成之前运行（与 Windows F11 同约束）。
"$BUILDER_PYTHON" "$PROJECT_ROOT/tools/portable_smoke_test.py" "$RELEASE_ROOT" --edition "$EDITION"

# L5（Full）：产物内 OCR 运行时可调用冒烟——确保拷进包的 tesseract/chromedriver 真可用
if [[ "$EDITION" == "Full" ]]; then
  echo "[Full] OCR 运行时冒烟..."
  "$RELEASE_ROOT/runtime/tesseract/tesseract" --tessdata-dir "$RELEASE_ROOT/runtime/tesseract/tessdata" \
    --list-langs >/dev/null 2>&1 || { echo "产物内 Tesseract 不可用" >&2; exit 1; }
  "$RELEASE_ROOT/runtime/selenium/chromedriver" --version >/dev/null 2>&1 \
    || { echo "产物内 ChromeDriver 不可用" >&2; exit 1; }
  echo "[Full] OCR 运行时冒烟通过"
fi

# 完整性清单：在新增 CAPABILITIES.json / RELEASE-INFO.json 之后生成，
# 使清单覆盖这两个机器可读文件（与 Windows 同序：先加文件再刷清单）。
"$BUILDER_PYTHON" "$PROJECT_ROOT/tools/create_runtime_manifest.py" --release-root "$RELEASE_ROOT"
"$RELEASE_ROOT/omnicrawler" runtime-verify --root "$RELEASE_ROOT"
# P5 完整版：Linux tar.xz 的容器级深校验在打包后执行（见下方 check_release_integrity
# --portable-tar --portable-deep），与 Windows 对 zip 的 --portable-zip --portable-deep 对齐。

# ---- 打包 tar.xz --------------------------------------------------------------
# 选 xz 而非 gzip：Linux Full 包 tar.gz -6 实测 2095MB，超 GitHub Release
# 单文件 2GiB 上限（run 32290393108/32299759729 上传被拒）；同样内容
# tar.xz -6 实测 1714MB（余量 334MB），零内容删减、零功能变化。
mkdir -p "$RELEASE_OUTPUT"
RELEASE_ARCHIVE="$RELEASE_OUTPUT/OmniCrawler-$APP_VERSION-Linux-Portable-$EDITION.tar.xz"
# 打包 tar.xz：排除运行期 logs/（与 RUNTIME-MANIFEST 的排除规则一致——
# create_runtime_manifest 不记录 logs/，深校验要求 tar 与 manifest 双向一致）。
tar -cJf "$RELEASE_ARCHIVE" -C "$BUILD_ROOT/release" --exclude='OmniCrawler/logs' OmniCrawler

# P5 完整版：Linux tar.xz 容器级深校验（与 Windows zip 对齐）。
# --portable-deep 对包内每个文件做 SHA-256 与 RUNTIME-MANIFEST 双向核对。
"$BUILDER_PYTHON" "$PROJECT_ROOT/tools/check_release_integrity.py" "$PROJECT_ROOT" \
  --portable-tar "$RELEASE_ARCHIVE" --portable-platform linux --portable-deep \
  || { echo "Linux portable tar.xz 深校验失败" >&2; exit 1; }

echo "Build staging: $RELEASE_ROOT"
echo "Portable archive: $RELEASE_ARCHIVE"
echo "CLI: $RELEASE_ROOT/omnicrawler --help"
