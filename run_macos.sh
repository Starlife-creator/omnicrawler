#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "[ERROR] Virtual environment not found. Run ./setup_macos.command first." >&2
  exit 1
fi
exec .venv/bin/python -m omnicrawl "$@"
