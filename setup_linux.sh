#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/python -m pip install -e '.[full,dev]'
export PLAYWRIGHT_BROWSERS_PATH="$PWD/.runtime/browsers"
.venv/bin/python -m playwright install --with-deps chromium

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

mkdir -p .runtime/models
.venv/bin/python tools/download_ocr_models.py .runtime/models/paddlex --source modelscope
.venv/bin/python -m omnicrawler capabilities --verify-imports
VERSION=$(.venv/bin/python -c "import omnicrawler; print(omnicrawler.__version__)")
echo "OmniCrawler $VERSION full Linux source environment is ready."
