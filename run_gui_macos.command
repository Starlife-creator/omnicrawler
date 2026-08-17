#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Virtual environment not found. Run setup_macos.command first."
  read -r -p "Press Enter to close..." _
  exit 1
fi
exec .venv/bin/python -m omnicrawler.gui "$@"
