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

    # **逐步上报**：哪一步炸就报哪一步，别让调用方只看到一句"退出码 1"。
    result: dict[str, Any] = {"ok": True, "engine": "paddle", "module": module_name}
    try:
        ocr = importlib.import_module(module_name)
    except Exception as exc:  # noqa: BLE001 - 组件 import 失败要如实回报
        result["ok"] = False
        result["stage"] = "import_component"
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    result["version"] = str(getattr(ocr, "__version__", "") or "")
    try:
        import paddle

        result["paddle_version"] = str(getattr(paddle, "__version__", "") or "")
        # **只验证组件自身依赖能否加载**，不调 `paddle.utils.run_check()`：
        # 那个函数检查的是**整机运行时**（Tesseract / ChromeDriver / CUDA），
        # 而 Standard 本来就不带 Tesseract/ChromeDriver —— 用它判组件成败，
        # 会把「组件没问题」误报成「组件坏了」（真实 CI 踩到）。
        #
        # 要证明的只是「原生扩展能在隔离子进程里 import 成功」：
        # 符号冲突 / 缺 DLL / ABI 不匹配都会在这步炸。
        # 用公开的顶层类型探针（不依赖 `paddle.base.core` 这种会随版本改动的路径）：
        _probe = paddle.Tensor  # type: ignore[attr-defined]
        result["backend"] = "ok" if _probe is not None else "missing"
        result["core_loaded"] = True
    except Exception as exc:  # noqa: BLE001 - 后端不可用要如实回报，不能吞
        result["ok"] = False
        result["stage"] = "backend"
        # **必须同时给 error**：`_extract_worker_error` 靠它拼错误详情。
        # 只设 `backend` 会让父进程拿不到原因，只剩一句「退出码 1」。
        result["backend"] = f"{type(exc).__name__}: {exc}"
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


#: engine → 适配器。新增引擎只需在这里登记，不必改 runner。
_ADAPTERS = {
    "generic": _probe_generic,
    "paddle": _probe_paddle,
}


def main() -> int:
    _trace("start")
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

    # ★ 清掉**已被遮蔽**的同名顶层包，让组件的版本真正生效。
    #
    # 问题形状：冻结包里的 `setuptools` 是 PyInstaller **按需裁剪**过的
    # （只收集被 import 到的子模块），而组件 `site-packages` 里那份是完整的。
    # 但 `sys.path.insert(0, ...)` 只影响**尚未 import** 的模块 ——
    # 如果 `setuptools` 已经被 import 过（`sys.modules` 里有），
    # 后续 `import setuptools.command.easy_install` 仍然命中旧的那份，
    # 于是报 `No module named 'setuptools.command.easy_install'`，
    # 而文件明明在组件的 site-packages 里（真实 CI 踩到）。
    #
    # 只清这几个「会被宿主预装、又会与组件版本冲突」的包。
    # 不能无脑 globals().clear()：那会连我们自己的模块一起丢。
    _shadow_prone = ("setuptools", "pkg_resources", "_distutils_hack", "distutils")
    dropped = []
    for _name in list(sys.modules):
        if _name.split(".")[0] in _shadow_prone:
            sys.modules.pop(_name, None)
            dropped.append(_name)
    if dropped:
        _trace(f"清除被遮蔽模块 {len(dropped)} 个：{sorted(set(d.split('.')[0] for d in dropped))}")

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

    _trace(f"dispatch engine={engine} module={module_name}")
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

    # 诊断：paddle 会 `from setuptools.command.easy_install import easy_install`，
    # 而这条会失败。先把**实际加载到的 setuptools** 打出来 ——
    # 否则只能看到「模块不存在」，分不清是「没装」还是「被别处的遮蔽」。
    try:
        import setuptools as _st

        _trace(f"setuptools={getattr(_st, '__file__', '?')} v={getattr(_st, '__version__', '?')}")
    except Exception as exc:  # noqa: BLE001 - 诊断而已，不能因此失败
        _trace(f"setuptools 探测失败：{type(exc).__name__}: {exc}")

    try:
        _trace("adapter_call")
        result = adapter(module_name, payload)
        _trace("adapter_done")
    except Exception as exc:  # noqa: BLE001 - 子进程边界，错误要回传给父进程
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1

    _trace(f"result ok={result.get('ok')}")
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result.get("ok") else 1


#: 诊断标记：worker 走到哪一步了。写到 stderr（父进程会带出来）。
#:
#: **为什么需要**：真实 CI 上出现过「已校验文件 14819 个」「退出码 1」
#: 但 stdout **完全没有 JSON** 的情况 —— 说明 worker 在写结果之前就被
#: 终止了（OOM / 段错误 / C 层 abort）。没有轨迹就无法区分「哪一步死的」。
def _trace(stage: str) -> None:
    print(f"[worker] {stage}", file=sys.stderr, flush=True)


def _guarded_main() -> int:
    """顶层兜底：**保证 stdout 一定有 JSON**。

    没有这一层时，任何在 `main()` 之外炸掉的错误（导入期异常、
    `SystemExit`、C 扩展在 init 阶段 segfault 前抛的错）都会让 stdout 为空，
    父进程只能看到 stderr 里一堆无关告警（真实 CI 踩到：
    `组件 ocr-paddle 退出码 1：内置 Tesseract 缺失 ...`，看不出真正原因）。

    用 `traceback.format_exc()` 把完整栈打进 JSON 的 `traceback` 字段。
    """
    import traceback

    try:
        return main()
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 - 兜底必须捕获一切
        print(
            json.dumps(
                {
                    "ok": False,
                    "stage": "worker_bootstrap",
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc()[-2000:],
                },
                ensure_ascii=False,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(_guarded_main())
