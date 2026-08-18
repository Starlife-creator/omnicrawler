"""Minimal JSON IPC host launched with Python isolated mode."""

from __future__ import annotations

import importlib
import json
import sys


def main() -> int:
    if len(sys.argv) != 3:
        return 2
    # B01-016：父进程用 -I（isolated）启动，PYTHONIOENCODING 不会生效；
    # 编码正确性在此坐实（子进程 stdout 始终 UTF-8，父进程按 utf-8 解码）。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
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
