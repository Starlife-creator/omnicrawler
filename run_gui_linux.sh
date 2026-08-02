#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [ ! -f ".venv/bin/python" ]; then
    echo "[ERROR] Virtual environment not found. Run ./setup_linux.sh first."
    exit 1
fi

exec .venv/bin/python -m omnicrawl.gui "$@"
