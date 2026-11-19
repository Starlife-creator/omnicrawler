"""Wheel-Pack 组件的**宿主内置 worker**：在子进程里 import 组件库并调用它。

## 为什么由宿主提供，而不是组件自带脚本

`asset_trust.py` 有一条铁律：**recipe 里不得出现可执行脚本**。
理由不是洁癖 —— recipe 来自市场，允许它携带脚本等于允许第三方代码
在用户机器上执行，而那份脚本的哈希只能校验「没被改」，不能校验「写得对」。

但 Wheel-Pack 组件（`paddle` / `paddleocr`）又确实需要在子进程里跑起来。
解法是把「怎么执行」固定在**宿主**这一侧：

* 组件只声明 `module`（要 import 哪个模块）与 `engine`（用哪套适配器）；
* worker 由宿主提供，是**受信任代码**，随宿主一起发布与校验；
* 组件侧贡献的只有**库**，没有可执行入口。

这样既满足「重型库在隔离子进程里加载」，又不打开「市场可以下发脚本」的口子。

## 运行时约定

worker 由 `component_runner.run_component()` 以 `python -m` 启动，读环境变量：

| 变量 | 含义 |
|---|---|
| `OMNICRAWL_COMPONENT_ROOT` | 组件根目录 |
| `OMNICRAWL_COMPONENT_SITE_PACKAGES` | 要挂进 `sys.path` 的目录 |
| `OMNICRAWL_COMPONENT_ENTRY` | 组件声明的**模块相对路径**（如 `site-packages/paddleocr/__init__.py`） |
| `OMNICRAWL_COMPONENT_ENGINE` | 适配器名（`paddle` / `generic`） |

协议：从 stdin 读一个 JSON 对象，向 stdout 写一个 JSON 对象。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def _resolve_module_name(entry: Path, site_packages: Path) -> str:
    """把 `site-packages/xxx/__init__.py` 换成可 import 的模块名 `xxx`。

    组件的 `entry` 是一个**文件路径**，但 worker 需要的是**模块名**。
    规则：
    * `__init__.py` → 其父目录名（包名）；
    * 其它 `.py` → 去掉后缀、把路径分隔符换成点。

    路径必须落在 `site-packages` 内，否则拒绝——防 `../` 越界。
    """
    try:
        relative = entry.resolve().relative_to(site_packages.resolve())
    except ValueError as exc:
        raise SystemExit(
            f"组件入口不在 site-packages 内：{entry}（拒绝越界路径）"
        ) from exc

    parts = list(relative.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    elif parts[-1].endswith(".py"):
        parts[-1] = parts[-1][: -len(".py")]
    if not parts:
        raise SystemExit("组件入口解析不出模块名")
    return ".".join(parts)


def _probe_generic(module_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """`generic` 引擎：只验证模块可 import，并回报其版本信息。

    这不是占位——它正是「组件装完能用」的最小判据：
    能 import 就说明原生扩展真的加载成功（符号冲突、缺 DLL 都会在这步炸）。
    """
    import importlib

    module = importlib.import_module(module_name)
    return {
        "ok": True,
        "engine": "generic",
        "module": module_name,
        "version": str(getattr(module, "__version__", "") or ""),
        "payload_keys": sorted(payload),
    }


def _probe_paddle(module_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """`paddle` 引擎：import `paddleocr`，并检查 PaddlePaddle 后端可用。"""
    import importlib

    ocr = importlib.import_module(module_name)
    result: dict[str, Any] = {
        "ok": True,
        "engine": "paddle",
        "module": module_name,
        "version": str(getattr(ocr, "__version__", "") or ""),
    }
    try:
        import paddle

        result["paddle_version"] = str(getattr(paddle, "__version__", "") or "")
        # `paddle.utils.run_check()` 会真的初始化后端；失败即说明原生库有问题。
        from paddle.utils import run_check  # type: ignore[import-not-found]

        run_check()
        result["backend"] = "ok"
    except Exception as exc:  # noqa: BLE001 - 后端不可用要如实回报，不能吞
        result["ok"] = False
        result["backend"] = f"{type(exc).__name__}: {exc}"
    return result


#: engine → 适配器。新增引擎只需在这里登记，不必改 runner。
_ADAPTERS = {
    "generic": _probe_generic,
    "paddle": _probe_paddle,
}


def main() -> int:
    site_packages = os.environ.get("OMNICRAWL_COMPONENT_SITE_PACKAGES", "").strip()
    entry_env = os.environ.get("OMNICRAWL_COMPONENT_ENTRY", "").strip()
    engine = os.environ.get("OMNICRAWL_COMPONENT_ENGINE", "generic").strip().lower() or "generic"

    if not site_packages or not entry_env:
        print(
            json.dumps(
                {"ok": False, "error": "缺少 OMNICRAWL_COMPONENT_SITE_PACKAGES 或 ENTRY"}
            )
        )
        return 2

    site_root = Path(site_packages)
    entry = Path(entry_env)
    if not entry.is_absolute():
        entry = Path(os.environ.get("OMNICRAWL_COMPONENT_ROOT", ".")) / entry

    # **只在该子进程内**挂 sys.path —— 宿主完全不知情。
    sys.path.insert(0, str(site_root))

    try:
        module_name = _resolve_module_name(entry, site_root)
    except SystemExit as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2

    try:
        raw = sys.stdin.read() or "{}"
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"stdin 不是合法 JSON：{exc}"}))
        return 2
    if not isinstance(payload, dict):
        payload = {"value": payload}

    adapter = _ADAPTERS.get(engine)
    if adapter is None:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"未登记的 engine：{engine!r}（支持 {sorted(_ADAPTERS)}）",
                }
            )
        )
        return 2

    try:
        result = adapter(module_name, payload)
    except Exception as exc:  # noqa: BLE001 - 子进程边界，错误要回传给父进程
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1

    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
