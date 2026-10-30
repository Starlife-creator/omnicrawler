#!/usr/bin/env bash
# OmniCrawler Linux GUI 启动器（源码目录内使用；便携包入口在 packaging/linux/）。
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "[ERROR] Virtual environment not found. Run ./setup_linux.sh first." >&2
  exit 1
fi

exec .venv/bin/python -m omnicrawler.gui "$@"
