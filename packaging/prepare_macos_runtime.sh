#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# prepare_macos_runtime.sh — macOS Full 便携包 OCR 运行时制备
#
# 生成产物（全部落 $RUNTIME_ROOT，随后由 build_macos.sh 拷进 .app/Contents/MacOS/runtime）：
#   runtime/tesseract/           Tesseract 二进制 + 本地 dylib（otool 递归解析 + 逐个重签）
#   runtime/tesseract/tessdata/  eng / chi_sim / osd 语言包
#   runtime/selenium/            ChromeDriver（Selenium Manager 按 bundled Chrome 匹配）
#   runtime/runtime-manifest.json
#
# 关键差异（相对 Linux L2）：
#   - Tesseract 来自 Homebrew（framework 环境无 apt）；必须把 otool -L 解析出的
#     dylib 树递归拷进本地并**逐个 ad-hoc codesign 重签**，否则 Gatekeeper 因
#     嵌入未签名二进制拦截（方案 5.3 M2）。
#   - **不制备 Paddle 模型**：macOS 无稳定 paddle wheel，OCR 走 PaddleOCR 3.x
#     Transformers 后端（方案 5.3 M3 评估项）；本脚本只保证 Tesseract+ChromeDriver。
# 用法：bash prepare_macos_runtime.sh \
#         --python <buildVenvPython> --runtime-root <path> --browsers-root <path>
#         [--cache-root <path>] [--skip-tesseract] [--skip-selenium]
# ---------------------------------------------------------------------------
set -euo pipefail

PYTHON=""
RUNTIME_ROOT=""
BROWSERS_ROOT=""
CACHE_ROOT="${TMPDIR:-/tmp}/OmniCrawler-runtime-assets-v1"
SKIP_TESSERACT=0
SKIP_SELENIUM=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --python) PYTHON="$2"; shift 2 ;;
    --runtime-root) RUNTIME_ROOT="$2"; shift 2 ;;
    --browsers-root) BROWSERS_ROOT="$2"; shift 2 ;;
    --cache-root) CACHE_ROOT="$2"; shift 2 ;;
    --skip-tesseract) SKIP_TESSERACT=1; shift ;;
    --skip-selenium) SKIP_SELENIUM=1; shift ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$PYTHON" || -z "$RUNTIME_ROOT" || -z "$BROWSERS_ROOT" ]]; then
  echo "用法: $0 --python <py> --runtime-root <dir> --browsers-root <dir> [--cache-root <dir>]" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
mkdir -p "$RUNTIME_ROOT" "$CACHE_ROOT"

log() { echo "[runtime-prep] $*"; }
die() { echo "[runtime-prep] ERROR: $*" >&2; exit 1; }

# ---- 定位 bundled Chromium（ChromeDriver 按它匹配）--------------------------
CHROME_BIN=""
while IFS= read -r candidate; do
  if [[ -n "$candidate" ]]; then CHROME_BIN="$candidate"; break; fi
done < <(find "$BROWSERS_ROOT" -type f \( -name 'Chromium' -o -name 'chrome' \) 2>/dev/null)
if [[ -z "$CHROME_BIN" ]]; then
  die "Playwright Chromium was not found under $BROWSERS_ROOT"
fi
log "Bundled Chromium: $CHROME_BIN"

# ---- ChromeDriver（Selenium Manager，按 bundled Chrome 匹配）----------------
if [[ "$SKIP_SELENIUM" -eq 0 ]]; then
  log "Prepare matching ChromeDriver via Selenium Manager..."
  MANAGER="$("$PYTHON" -c "import os, selenium; print(os.path.join(os.path.dirname(selenium.__file__), 'webdriver', 'common', 'macos', 'selenium-manager'))")"
  [[ -x "$MANAGER" ]] || die "Selenium Manager not found: $MANAGER"
  SELENIUM_CACHE="$CACHE_ROOT/selenium"
  mkdir -p "$RUNTIME_ROOT/selenium"
  MANAGER_OUTPUT="$("$MANAGER" --browser chrome --browser-path "$CHROME_BIN" \
      --cache-path "$SELENIUM_CACHE" --avoid-browser-download --skip-driver-in-path \
      --skip-browser-in-path --avoid-stats --timeout 600 --output JSON 2>/dev/null)" \
    || die "Selenium Manager failed"
  DRIVER_SOURCE="$(printf '%s' "$MANAGER_OUTPUT" | "$PYTHON" -c "import json,sys; print(json.load(sys.stdin)['result']['driver_path'])")"
  [[ -n "$DRIVER_SOURCE" && -f "$DRIVER_SOURCE" ]] || die "ChromeDriver was not downloaded: $DRIVER_SOURCE"
  cp "$DRIVER_SOURCE" "$RUNTIME_ROOT/selenium/chromedriver"
  chmod +x "$RUNTIME_ROOT/selenium/chromedriver"
  log "ChromeDriver: $("$RUNTIME_ROOT/selenium/chromedriver" --version)"
fi

# ---- Tesseract（Homebrew 取包 + dylib 树拷贝 + 逐个重签）----------------------
if [[ "$SKIP_TESSERACT" -eq 0 ]]; then
  log "Prepare Tesseract runtime from Homebrew..."
  command -v brew >/dev/null 2>&1 || die "需要 Homebrew"
  brew list tesseract >/dev/null 2>&1 || brew install tesseract >/dev/null 2>&1
  TESS_BIN="$(command -v tesseract)" || die "tesseract 未安装"
  TESS_ROOT="$RUNTIME_ROOT/tesseract"
  TESSDATA_ROOT="$TESS_ROOT/tessdata"
  mkdir -p "$TESS_ROOT" "$TESSDATA_ROOT"

  # 拷贝 tesseract 二进制 + 递归 dylib 树（otool -L 解析，含 leptonica 等）
  cp "$TESS_BIN" "$TESS_ROOT/tesseract"
  declare -A SEEN_LIB
  resolve_dylibs() { # $1 = 二进制路径；$2 = 目标目录
    local bin="$1" target="$2"
    for dep in $(otool -L "$bin" 2>/dev/null | tail -n +2 | awk '{print $1}'); do
      case "$dep" in
        /usr/lib/*|/System/*|@rpath/*|@loader_path/*|@executable_path/*) continue ;;
        *)
          if [[ -f "$dep" ]] && [[ -z "${SEEN_LIB["$dep"]:-}" ]]; then
            SEEN_LIB["$dep"]=1
            cp "$dep" "$target/"
            resolve_dylibs "$dep" "$target"
          fi
          ;;
      esac
    done
  }
  resolve_dylibs "$TESS_BIN" "$TESS_ROOT"

  # 重写 dylib 安装名：让 tesseract 优先找本地 lib（install_name_tool 改 @loader_path）
  for lib in "$TESS_ROOT"/*.dylib; do
    [[ -e "$lib" ]] || continue
    install_name_tool -id "@loader_path/$(basename "$lib")" "$lib" 2>/dev/null || true
  done
  # tesseract 自身对 libtesseract/liblept 的引用改指本地（不依赖 brew 前缀）
  install_name_tool -change "$(otool -L "$TESS_BIN" | grep -o '/[^ ]*libtesseract[^ ]*' | head -1)" \
      "@loader_path/libtesseract.dylib" "$TESS_ROOT/tesseract" 2>/dev/null || true
  install_name_tool -change "$(otool -L "$TESS_BIN" | grep -o '/[^ ]*liblept[^ ]*' | head -1)" \
      "@loader_path/liblept.dylib" "$TESS_ROOT/tesseract" 2>/dev/null || true

  # 逐个 ad-hoc 重签（Gatekeeper 会拦截未签名/失效签名的嵌入二进制）
  codesign --force --sign - "$TESS_ROOT/tesseract" 2>/dev/null || true
  for lib in "$TESS_ROOT"/*.dylib; do
    [[ -e "$lib" ]] || continue
    codesign --force --sign - "$lib" 2>/dev/null || true
  done

  # tessdata 语言包（tessdata_fast）
  for language in eng chi_sim osd; do
    lang_path="$TESSDATA_ROOT/$language.traineddata"
    if [[ ! -f "$lang_path" || "$(stat -f %z "$lang_path")" -lt 100000 ]]; then
      log "Download tessdata_fast/$language.traineddata ..."
      curl -fsSL "https://github.com/tesseract-ocr/tessdata_fast/raw/main/$language.traineddata" \
        -o "$lang_path.part"
      mv "$lang_path.part" "$lang_path"
    fi
    MAGIC="$(head -c 1 "$lang_path" | od -An -tx1 | tr -d ' ')"
    if [[ "$MAGIC" == "3c" ]]; then
      die "tessdata 语言包看起来是 HTML 错误页: $lang_path"
    fi
  done

  # Tesseract 冒烟
  "$TESS_ROOT/tesseract" --tessdata-dir "$TESSDATA_ROOT" --list-langs >/dev/null 2>&1 \
    || die "Tesseract 语言验证失败"
  log "Tesseract ready"
fi

# ---- runtime-manifest.json --------------------------------------------------
"$PYTHON" - "$RUNTIME_ROOT" "$CHROME_BIN" <<'PYEOF'
import datetime, hashlib, json, os, sys
from pathlib import Path
runtime_root = Path(sys.argv[1])
chrome = sys.argv[2]
files = []
total = 0
for p in sorted(runtime_root.rglob("*")):
    if p.is_file():
        total += p.stat().st_size
        files.append({
            "path": str(p.relative_to(runtime_root)).replace(os.sep, "/"),
            "bytes": p.stat().st_size,
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        })
manifest = {
    "schema": 1,
    "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "chrome": chrome,
    "files": len(files),
    "bytes": total,
    "sha256": files,
}
(runtime_root / "runtime-manifest.json").write_text(
    json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
)
print(f"macOS runtime ready: {runtime_root}")
PYEOF
