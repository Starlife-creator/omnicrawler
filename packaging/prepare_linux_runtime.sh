#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# prepare_linux_runtime.sh — Linux Full 便携包 OCR 运行时制备
#
# 生成产物（全部落 $RUNTIME_ROOT，随后由 build_linux.sh 拷进 RELEASE_ROOT）：
#   runtime/tesseract/           Tesseract 二进制 + 本地 lib（patchelf 指 rpath）
#   runtime/tesseract/tessdata/  eng / chi_sim / osd 语言包
#   runtime/selenium/            ChromeDriver（Selenium Manager 按 bundled Chrome 匹配）
#   runtime/models/paddlex/      PaddleOCR 推理模型（download_and_smoke_test.py）
#   runtime/runtime-manifest.json
#
# 与 Windows prepare_windows_runtime.ps1 对齐的资产/门禁分工（B12-005）：
#   - Tesseract：来自发行版 apt 源（apt 自带包签名校验），本脚本做解包 + rpath
#     + ldd 门禁；tessdata 来自 tessdata_fast 上游，做 HTML 错误页与最小值校验。
#   - ChromeDriver：Selenium Manager 管理（自动按 bundled Chrome 匹配版本 + 哈希）。
#   - PaddleOCR 模型：download_and_smoke_test.py（CDN、无哈希钉，仅冒烟，属已知边界）。
# 用法：bash prepare_linux_runtime.sh \
#         --python <buildVenvPython> --runtime-root <path> --browsers-root <path>
#         [--cache-root <path>] [--skip-ocr-models] [--skip-tesseract] [--skip-selenium]
# ---------------------------------------------------------------------------
set -euo pipefail

PYTHON=""
RUNTIME_ROOT=""
BROWSERS_ROOT=""
CACHE_ROOT="${TMPDIR:-/tmp}/OmniCrawler-runtime-assets-v1"
SKIP_OCR_MODELS=0
SKIP_TESSERACT=0
SKIP_SELENIUM=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --python) PYTHON="$2"; shift 2 ;;
    --runtime-root) RUNTIME_ROOT="$2"; shift 2 ;;
    --browsers-root) BROWSERS_ROOT="$2"; shift 2 ;;
    --cache-root) CACHE_ROOT="$2"; shift 2 ;;
    --skip-ocr-models) SKIP_OCR_MODELS=1; shift ;;
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
done < <(find "$BROWSERS_ROOT" -type f \( -name chrome -o -name chromium \) 2>/dev/null)
if [[ -z "$CHROME_BIN" ]]; then
  die "Playwright Chromium was not found under $BROWSERS_ROOT"
fi
log "Bundled Chromium: $CHROME_BIN"

# ---- ChromeDriver（Selenium Manager 按 bundled Chrome 匹配，动态解决 S2）----
if [[ "$SKIP_SELENIUM" -eq 0 ]]; then
  log "Prepare matching ChromeDriver via Selenium Manager..."
  MANAGER="$("$PYTHON" -c "import os, selenium; print(os.path.join(os.path.dirname(selenium.__file__), 'webdriver', 'common', 'linux', 'selenium-manager'))")"
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

# ---- Tesseract（apt 源取包 + dpkg 解包 + patchelf 指本地 rpath + ldd 门禁）----
if [[ "$SKIP_TESSERACT" -eq 0 ]]; then
  log "Prepare Tesseract runtime from apt packages..."
  command -v patchelf >/dev/null 2>&1 || die "需要 patchelf（apt install -y patchelf）"
  TESS_ROOT="$RUNTIME_ROOT/tesseract"
  TESSDATA_ROOT="$TESS_ROOT/tessdata"
  TESS_CACHE="$CACHE_ROOT/tesseract-debs"
  mkdir -p "$TESS_ROOT" "$TESSDATA_ROOT" "$TESS_CACHE"

  # 解包所有 tesseract 相关 .deb 到统一根，再收集二进制与 .so
  EXTRACT_ROOT="$TESS_CACHE/extracted"
  rm -rf "$EXTRACT_ROOT"
  mkdir -p "$EXTRACT_ROOT"
  DEB_DIR="$TESS_CACHE/debs"
  rm -rf "$DEB_DIR"
  mkdir -p "$DEB_DIR"

  # 下载 tesseract-ocr 及其直接 Depends（apt 源签名保证完整性）
  DEB_PKGS=(tesseract-ocr)
  DEB_PKGS+=($(apt-cache depends --recurse --no-recommends --no-suggests --no-conflicts \
      --no-breaks --no-replaces --no-enhances --no-pre-depends tesseract-ocr 2>/dev/null \
      | awk '/^[a-z]/ {print $1}'))
  # 只保留真实存在的包，避免 apt-get download 对虚拟包报错
  EXISTING=()
  for pkg in "${DEB_PKGS[@]}"; do
    if apt-cache show "$pkg" >/dev/null 2>&1; then EXISTING+=("$pkg"); fi
  done
  log "Downloading ${#EXISTING[@]} apt packages: ${EXISTING[*]}"
  (cd "$DEB_DIR" && apt-get download "${EXISTING[@]}" >/dev/null 2>&1) || die "apt-get download failed（需先 apt-get update）"

  # dpkg-deb 解包全部 .deb，合并 usr/ 前缀
  for deb in "$DEB_DIR"/*.deb; do
    dpkg-deb -x "$deb" "$EXTRACT_ROOT"
  done
  if [[ ! -x "$EXTRACT_ROOT/usr/bin/tesseract" ]]; then
    die "tesseract 二进制未从 apt 包解出"
  fi

  # 拷贝二进制与共享库到运行时目录（排除系统级文档）
  cp "$EXTRACT_ROOT/usr/bin/tesseract" "$TESS_ROOT/"
  find "$EXTRACT_ROOT/usr/lib" -type f -name '*.so*' 2>/dev/null | while read -r so; do
    cp -L "$so" "$TESS_ROOT/"
  done
  # lib 目录也可能在 usr/lib/x86_64-linux-gnu（dpkg-deb -x 合并后路径），
  # 兜底：直接复制任何 .so 到 tesseract 同级
  find "$EXTRACT_ROOT/usr/lib" -type f -name '*.so*' 2>/dev/null | wc -l | grep -q '^[1-9]' \
    || die "apt 包未解出任何共享库"

  # patchelf 指 rpath 到 $ORIGIN（本地 lib），保证不依赖系统包
  patchelf --set-rpath '$ORIGIN' "$TESS_ROOT/tesseract" || true

  # ldd 门禁：不得有 not found
  MISSING="$(ldd "$TESS_ROOT/tesseract" 2>/dev/null | grep -c 'not found' || true)"
  if [[ "$MISSING" -gt 0 ]]; then
    log "ldd 检测到未解析依赖（部分库仍取系统版本，尝试修补）："
    ldd "$TESS_ROOT/tesseract" 2>/dev/null | grep 'not found' || true
    die "Tesseract 依赖未完全本地化"
  fi

  # tessdata 语言包（tessdata_fast，非压缩 LSTM blob）
  for language in eng chi_sim osd; do
    lang_path="$TESSDATA_ROOT/$language.traineddata"
    if [[ ! -f "$lang_path" || "$(stat -c %s "$lang_path")" -lt 100000 ]]; then
      log "Download tessdata_fast/$language.traineddata ..."
      curl -fsSL "https://github.com/tesseract-ocr/tessdata_fast/raw/main/$language.traineddata" \
        -o "$lang_path.part"
      mv "$lang_path.part" "$lang_path"
    fi
    # 拒绝 HTML 错误页（GitHub 404/限流会返回 HTML 但超过最小值）
    MAGIC="$(head -c 1 "$lang_path" | od -An -tx1 | tr -d ' ')"
    if [[ "$MAGIC" == "3c" ]]; then
      die "tessdata 语言包看起来是 HTML 错误页: $lang_path"
    fi
  done

  # Tesseract 冒烟：列语言 + chi_sim 实际识别
  "$TESS_ROOT/tesseract" --tessdata-dir "$TESSDATA_ROOT" --list-langs >/dev/null 2>&1 \
    || die "Tesseract 语言验证失败"
  PROBE_PNG="$TESS_CACHE/ocr_probe.png"
  "$PYTHON" - "$PROBE_PNG" <<'PYEOF'
import sys
from PIL import Image, ImageDraw
img = Image.new("RGB", (560, 100), "white")
ImageDraw.Draw(img).text((20, 30), "OmniCrawler OCR 123", fill="black")
img.save(sys.argv[1])
PYEOF
  "$TESS_ROOT/tesseract" "$PROBE_PNG" stdout --tessdata-dir "$TESSDATA_ROOT" -l chi_sim >/dev/null 2>&1 \
    || die "Tesseract chi_sim 实际识别失败（语言包可能损坏）"
  rm -f "$PROBE_PNG"
  log "Tesseract ready"
fi

# ---- PaddleOCR 模型（复用跨平台下载器）-------------------------------------
if [[ "$SKIP_OCR_MODELS" -eq 0 ]]; then
  log "Prepare and verify PPStructureV3 offline models..."
  "$PYTHON" "$PROJECT_ROOT/tools/download_and_smoke_test.py" \
      "$RUNTIME_ROOT/models/paddlex" --source aistudio \
    || die "PaddleOCR model verification failed"
fi

# ---- runtime-manifest.json（与 Windows 对齐）--------------------------------
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
print(f"Linux runtime ready: {runtime_root}")
PYEOF
