"""Minimal JSON IPC host launched with Python isolated mode."""

from __future__ import annotations

import importlib
import json
import sys


def main() -> int:
    if len(sys.argv) != 3:
        return 2
    sys.path.insert(0, sys.argv[2])
    request = json.loads(sys.stdin.read())
    module = importlib.import_module(sys.argv[1])
    handler = getattr(module, "handle", None)
    if not callable(handler):
        raise RuntimeError("插件必须导出 handle(operation, payload)")
    result = handler(str(request["operation"]), dict(request.get("payload", {})))
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
