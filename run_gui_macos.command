#!/usr/bin/env bash
# OmniCrawler macOS GUI 启动器（访达双击时由 Terminal 执行 .command）。
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "[ERROR] Virtual environment not found. Run setup_macos.command first." >&2
  read -r -p "Press Enter to close..." _
  exit 1
fi

exec .venv/bin/python -m omnicrawler.gui "$@"
