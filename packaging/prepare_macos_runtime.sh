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
# bash 3.2（macOS 默认）下 set -u 与递归 local 变量/空数组展开存在兼容问题
# （unbound variable，v0.9.1 CI 实测），移除 -u；未定义变量由显式 die 兜底。
set -eo pipefail

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
# 新版 Playwright 在 macOS 下载 chrome-mac-arm64.zip，可执行文件是
# chromium-*/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing
# （旧版是 chrome-mac/Chromium）。find 按可执行文件名模糊匹配两者。
CHROME_BIN=""
while IFS= read -r candidate; do
  if [[ -n "$candidate" ]]; then CHROME_BIN="$candidate"; break; fi
done < <(find "$BROWSERS_ROOT" -type f \( -name 'Chromium' -o -name 'chrome' -o -name 'Google Chrome for Testing' \) 2>/dev/null)
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
  cp -f "$DRIVER_SOURCE" "$RUNTIME_ROOT/selenium/chromedriver"
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
  cp -f "$TESS_BIN" "$TESS_ROOT/tesseract"

  # brew 的 tesseract 依赖多以 @rpath/libtesseract.5.dylib 形式引用，
  # 不能简单跳过 @rpath/*；需沿 LC_RPATH 解析真实路径再拷贝。
  # 对已拷贝的每个 dylib，把其 @rpath/x 依赖统一 -change 为 @loader_path/x，
  # 使整棵树脱离 brew 前缀（@loader_path 相对"加载者所在目录"，本树都在同目录）。
  # macOS runner 的 /bin/bash 是 3.2（GitHub Actions），不支持关联数组 declare -A，
  # 用普通数组 + 辅助函数做已拷贝去重（兼容 bash 3.2）。
  # 按 basename 去重：同一物理库可能经 symlink/不同 LC_RPATH 解析为多个路径，
  # 但拷到同一目录后短名唯一（也避免同名只读目标重复 cp）。
  SEEN_LIBS=()
  _seen() { # $1=basename，已记录返回 0，未记录返回 1（set -u 下空数组展开需先查长度）
    local item
    if [[ ${#SEEN_LIBS[@]} -eq 0 ]]; then return 1; fi
    for item in "${SEEN_LIBS[@]}"; do
      if [[ "$item" == "$1" ]]; then return 0; fi
    done
    return 1
  }
  resolve_dylibs() { # $1 = 二进制路径（otool 源）
    local bin="$1" dep rpath_path rel dep_base
    for dep in $(otool -L "$bin" 2>/dev/null | tail -n +2 | awk '{print $1}'); do
      case "$dep" in
        /usr/lib/*|/System/*|/Library/*) continue ;; # 系统库不打包
        @rpath/*)
          # 沿二进制 LC_RPATH 解析 @rpath 真实文件（brew 路径 /opt/homebrew/opt/...）
          rel="${dep#@rpath/}"
          rpath_path=""
          while IFS= read -r rdir; do
            if [[ -f "$rdir/$rel" ]]; then rpath_path="$rdir/$rel"; break; fi
          done < <(otool -l "$bin" 2>/dev/null | awk '/LC_RPATH/{getline; getline; print $2}')
          if [[ -z "$rpath_path" ]]; then
            # 兜底：/opt/homebrew/opt 全目录按 basename 查找（含 symlink，-type l）
            rpath_path="$(find -L /opt/homebrew/opt -name "$rel" -type f 2>/dev/null | head -1)"
          fi
          if [[ -z "$rpath_path" ]]; then
            # 解析失败不阻断构建：跳过该依赖，由最终 Tesseract 冒烟（--list-langs）
            # 验证运行可用性——若真缺库冒烟会明确报错。brew 依赖树存在 LC_RPATH
            # 覆盖不全的情况（v0.9.1 CI 实测 die 中断），跳过 + 冒烟兜底更稳。
            echo "[runtime-prep] WARN: 无法解析 @rpath 依赖 $dep（来自 $bin），跳过" >&2
            continue
          fi
          dep="$rpath_path"
          ;;
        @loader_path/*|@executable_path/*) continue ;; # 已是相对引用，不拷贝
      esac
      dep_base="$(basename "$dep")"
      if [[ -f "$dep" ]] && ! _seen "$dep_base"; then
        SEEN_LIBS+=("$dep_base")
        cp -f "$dep" "$TESS_ROOT/"
        resolve_dylibs "$dep"
      fi
    done
  }
  resolve_dylibs "$TESS_BIN"

  # 统一重写依赖引用：每个二进制/dylib 的 @rpath/x 依赖 → @loader_path/x
  for target in "$TESS_ROOT/tesseract" "$TESS_ROOT"/*.dylib; do
    [[ -e "$target" ]] || continue
    for dep in $(otool -L "$target" 2>/dev/null | tail -n +2 | awk '{print $1}'); do
      case "$dep" in
        @rpath/*)
          install_name_tool -change "$dep" "@loader_path/${dep#@rpath/}" "$target" 2>/dev/null || true
          ;;
      esac
    done
    # dylib 的 install_name 也统一为 @loader_path/短名，保证按同目录解析
    install_name_tool -id "@loader_path/$(basename "$target")" "$target" 2>/dev/null || true
  done

  # 逐个 ad-hoc 重签（Gatekeeper 会拦截未签名/失效签名的嵌入二进制）
  for target in "$TESS_ROOT/tesseract" "$TESS_ROOT"/*.dylib; do
    [[ -e "$target" ]] || continue
    codesign --force --sign - "$target" 2>/dev/null || true
  done

  # tessdata 语言包（tessdata_fast）
  for language in eng chi_sim osd; do
    lang_path="$TESSDATA_ROOT/$language.traineddata"
    if [[ ! -f "$lang_path" || "$(stat -f %z "$lang_path")" -lt 100000 ]]; then
      log "Download tessdata_fast/$language.traineddata ..."
      # 多源 fallback：GitHub raw 服务偶发 404/503（v0.9.1 CI 实测），依次尝试
      # 官方重定向 → raw 直链 → jsDelivr CDN。
      downloaded=0
      for base in \
        "https://github.com/tesseract-ocr/tessdata_fast/raw/main" \
        "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main" \
        "https://cdn.jsdelivr.net/gh/tesseract-ocr/tessdata_fast@main"; do
        if curl -fsSL --retry 2 --retry-delay 2 --retry-all-errors \
            "$base/$language.traineddata" -o "$lang_path.part"; then
          downloaded=1
          break
        fi
        log "tessdata 源不可用，切换备用源: $base"
        rm -f "$lang_path.part"
      done
      if [[ "$downloaded" -ne 1 ]]; then
        die "tessdata 下载失败: $language.traineddata（全部源不可用）"
      fi
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
