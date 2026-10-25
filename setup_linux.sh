#!/usr/bin/env bash
# OmniCrawler Linux 源码环境初始化（C-#18 / C-#24）。
#
# 环境变量：
#   OMNICRAW_PROFILE=minimal|standard|full   依赖档位（默认 standard）
#   OMNICRAW_EXTRAS=html,gui,...             显式 extras，覆盖 profile
#   OMNICRAW_PIP_INDEX_URL=https://...       pip 镜像源（默认 PyPI）
#   OMNICRAW_PIP_EXTRA_INDEX_URL=https://... 附加索引（可选）
#   OMNICRAW_MODEL_SOURCE=modelscope|aistudio|huggingface  模型下载源（默认 modelscope）
#   OMNICRAWL_SKIP_SYSTEM_PACKAGES=1         跳过系统 Tesseract 安装
set -euo pipefail
cd "$(dirname "$0")"

# ---- Python ≥3.12 版本断言（C-#18）：装依赖之前当场失败，报错点贴近原因 ----
PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! "$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
  echo "[ERROR] 需要 Python ≥3.12（当前：$("$PYTHON_BIN" --version 2>&1 || echo '未找到 python3')）。" >&2
  exit 1
fi

# ---- 依赖档位（C-#24）：minimal 基础 / standard 本地开发常用 / full 重型全家桶 ----
PROFILE="${OMNICRAW_PROFILE:-standard}"
case "$PROFILE" in
  minimal)
    EXTRAS="html,gui,async-http"
    ;;
  standard)
    EXTRAS="gui,html,async-http,browser,pdf,document,security,tls,storage,streams"
    ;;
  full)
    EXTRAS="full,dev"
    ;;
  *)
    echo "[ERROR] 未知依赖档位 OMNICRAW_PROFILE=$PROFILE（minimal|standard|full）" >&2
    exit 2
    ;;
esac
# 显式 extras 覆盖 profile（例如 OMNICRAW_EXTRAS="paddleocr,selenium"）。
if [ -n "${OMNICRAW_EXTRAS:-}" ]; then
  EXTRAS="$OMNICRAW_EXTRAS"
fi

# ---- 镜像源可配置（C-#18）----
PIP_INDEX_ARGS=()
if [ -n "${OMNICRAW_PIP_INDEX_URL:-}" ]; then
  PIP_INDEX_ARGS+=(-i "$OMNICRAW_PIP_INDEX_URL")
fi
if [ -n "${OMNICRAW_PIP_EXTRA_INDEX_URL:-}" ]; then
  PIP_INDEX_ARGS+=(--extra-index-url "$OMNICRAW_PIP_EXTRA_INDEX_URL")
fi

"$PYTHON_BIN" -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel "${PIP_INDEX_ARGS[@]}"
.venv/bin/python -m pip install -e ".[$EXTRAS]" "${PIP_INDEX_ARGS[@]}"

# 浏览器二进制只在非 minimal 档位安装（minimal 为无头基础环境）。
if [ "$PROFILE" != "minimal" ]; then
  export PLAYWRIGHT_BROWSERS_PATH="$PWD/.runtime/browsers"
  .venv/bin/python -m playwright install --with-deps chromium
fi

if ! command -v tesseract >/dev/null 2>&1 && [ "${OMNICRAWL_SKIP_SYSTEM_PACKAGES:-0}" != "1" ]; then
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y tesseract-ocr tesseract-ocr-eng tesseract-ocr-chi-sim
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y tesseract tesseract-langpack-eng tesseract-langpack-chi_sim
  else
    echo "[WARN] Install Tesseract and eng/chi_sim language data with your system package manager."
  fi
fi

# 重型 PaddleOCR 离线模型只在 full 档位下载（standard/minimal 不触发大模型拉取）。
if [ "$PROFILE" = "full" ]; then
  OMNICRAW_MODEL_SOURCE="${OMNICRAW_MODEL_SOURCE:-modelscope}"
  case "$OMNICRAW_MODEL_SOURCE" in
    modelscope|aistudio|huggingface) ;;
    *)
      echo "[ERROR] 未知模型源 OMNICRAW_MODEL_SOURCE=$OMNICRAW_MODEL_SOURCE（modelscope|aistudio|huggingface）" >&2
      exit 2
      ;;
  esac
  mkdir -p .runtime/models
  .venv/bin/python tools/download_and_smoke_test.py .runtime/models/paddlex --source "$OMNICRAW_MODEL_SOURCE"
fi

.venv/bin/python -m omnicrawler capabilities --verify-imports
VERSION=$(.venv/bin/python -c "import omnicrawler; print(omnicrawler.__version__)")
echo "OmniCrawler $VERSION Linux source environment ready (profile=$PROFILE, extras=$EXTRAS)."
