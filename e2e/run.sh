#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${PYTHON:-python}"
args=()
for argument in "$@"; do
  case "$argument" in
    --install) "$python_bin" -m pip install -e "$repo_root[html,pdf,browser,dev]" ;;
    --browser|--full-regression) args+=("$argument") ;;
    *) echo "Unknown argument: $argument" >&2; exit 2 ;;
  esac
done
exec "$python_bin" "$repo_root/e2e/run.py" "${args[@]}"
