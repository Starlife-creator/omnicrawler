#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/python -m pip install -e '.[full,dev]'
export PLAYWRIGHT_BROWSERS_PATH="$PWD/.runtime/browsers"
.venv/bin/python -m playwright install chromium

if ! command -v tesseract >/dev/null 2>&1 && [ "${OMNICRAWL_SKIP_SYSTEM_PACKAGES:-0}" != "1" ]; then
  if command -v brew >/dev/null 2>&1; then
    brew install tesseract tesseract-lang
  else
    echo "[WARN] Homebrew was not found; install Tesseract manually or use PaddleOCR."
  fi
fi

mkdir -p .runtime/models
.venv/bin/python tools/download_ocr_models.py .runtime/models/paddlex --source modelscope
.venv/bin/python -m omnicrawl capabilities --verify-imports
VERSION=$(.venv/bin/python -c "import omnicrawl; print(omnicrawl.__version__)")
echo "OmniCrawler $VERSION full macOS source environment is ready."
