"""Wheel-Pack 组件运行器：在**隔离子进程**里挂载已校验的 site-packages。

## 为什么必须隔离子进程

`paddle` / `cv2` / `scipy` 这类重型 C 扩展与宿主已加载的 `PySide6` / `lxml`
同处一个进程时，极易触发 `libiomp5md.dll`、`msvcp140`、OpenSSL 符号冲突；
Linux 下 `libmklml_intel.so` 还要求**进程启动前**设置环境变量，进程内后加载
直接报错。所以只在该子进程内挂载，GUI 主进程零污染。

## 安全边界（三条，缺一不可）

1. **零 pip、零安装脚本**：PEP 427 下 `.whl` 就是纯数据 ZIP，装配时已由
   `component_recipe` 逐个校验 64 位 SHA-256 后解压。本模块**不做任何安装动作**，
   只把已落盘的 `site-packages` 挂进子进程的 `sys.path`。
2. **启动前二次核验**：组件目录可能在上次校验之后被改动。子进程启动前重新
   校验 `component.json` 里声明的每个文件哈希——不信任"上次校验过"。
3. **只在该子进程内**：`sys.path` 与原生库搜索路径的修改都发生在子进程内，
   宿主进程完全不知情。父进程只负责构造命令与转发 JSON。

## 与原生可执行组件的区别

`ocr-tesseract` 这类 C++ 二进制 + `.traineddata` 走**原生子进程**（直接 exec）；
Wheel-Pack 走**Python 子进程**（`python -m ...`）。两套模式在
`component_recipe` 的 `entry.runner` 上区分：`native` / `worker-wheelpack`。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: 子进程里代表"组件入口"的环境变量前缀。用环境变量而不是命令行参数，
#: 是为了让入口路径不出现在进程列表里（路径可能含用户名等本机信息）。
_ENV_COMPONENT_ROOT = "OMNICRAWL_COMPONENT_ROOT"
_ENV_SITE_PACKAGES = "OMNICRAWL_COMPONENT_SITE_PACKAGES"
_ENV_ENTRY = "OMNICRAWL_COMPONENT_ENTRY"
_ENV_ENGINE = "OMNICRAWL_COMPONENT_ENGINE"


class ComponentRunnerError(RuntimeError):
    """组件运行失败（校验不过、入口缺失、子进程异常退出等）。"""


@dataclass(frozen=True, slots=True)
class WheelPackSpec:
    """一个已装配 Wheel-Pack 组件的运行规格。"""

    name: str
    version: str
    component_root: Path
    site_packages: Path
    entry: Path
    engine: str


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def verify_component_files(component_root: Path) -> int:
    """按 `component.json` 声明的哈希重新校验组件目录，返回校验文件数。

    **不信任"装配时校验过"**：组件目录在两次使用之间可能被改动，
    而子进程一旦启动就无法再拦截。所以每次启动前重新算一遍。
    """
    root = Path(component_root)
    manifest_path = root / "component.json"
    if not manifest_path.is_file():
        raise ComponentRunnerError(f"组件缺少 component.json：{manifest_path}")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ComponentRunnerError(f"component.json 无法解析：{exc}") from exc

    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, dict) or not files:
        raise ComponentRunnerError("component.json 未声明任何文件，拒绝加载")

    checked = 0
    for relative, expected in files.items():
        target = (root / str(relative)).resolve()
        # 路径穿越拦截：声明里出现 `..` 或绝对路径一律拒绝。
        try:
            target.relative_to(root.resolve())
        except ValueError as exc:
            raise ComponentRunnerError(
                f"组件清单声明的路径越界：{relative}"
            ) from exc
        if not target.is_file():
            raise ComponentRunnerError(f"组件文件缺失：{relative}")
        actual = _sha256(target)
        expected_text = str(expected).replace("sha256:", "").strip().lower()
        if actual != expected_text:
            raise ComponentRunnerError(
                f"组件文件哈希不符：{relative}（期望 {expected_text[:16]}…，"
                f"实际 {actual[:16]}…）"
            )
        checked += 1
    return checked


def build_wheelpack_spec(component_root: Path) -> WheelPackSpec:
    """从已装配的组件目录读出运行规格。

    期望的目录结构（由 `component_recipe` 装配产生）::

        <component_root>/
            component.json          # 清单（含 files 哈希）
            site-packages/          # wheel 解压内容
            entry.json              # 入口声明（name/version/engine/entry）
    """
    root = Path(component_root)
    site_packages = root / "site-packages"
    if not site_packages.is_dir():
        raise ComponentRunnerError(
            f"组件缺少 site-packages 目录：{site_packages}（不是 Wheel-Pack 形态？）"
        )

    entry_file = root / "entry.json"
    if not entry_file.is_file():
        raise ComponentRunnerError(f"组件缺少 entry.json：{entry_file}")
    try:
        entry_data = json.loads(entry_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ComponentRunnerError(f"entry.json 无法解析：{exc}") from exc
    if not isinstance(entry_data, dict):
        raise ComponentRunnerError("entry.json 顶层必须是对象")

    entry_rel = str(entry_data.get("entry") or "").strip()
    if not entry_rel:
        raise ComponentRunnerError("entry.json 缺少 entry 字段")
    entry_path = (root / entry_rel).resolve()
    try:
        entry_path.relative_to(root.resolve())
    except ValueError as exc:
        raise ComponentRunnerError(f"entry 路径越界：{entry_rel}") from exc
    if not entry_path.is_file():
        raise ComponentRunnerError(f"组件入口不存在：{entry_rel}")

    return WheelPackSpec(
        name=str(entry_data.get("name") or root.name),
        version=str(entry_data.get("version") or ""),
        component_root=root.resolve(),
        site_packages=site_packages.resolve(),
        entry=entry_path,
        engine=str(entry_data.get("engine") or "generic").strip().lower(),
    )


def component_runner_env(spec: WheelPackSpec) -> dict[str, str]:
    """构造子进程环境：注入组件路径，并把原生库搜索路径前置。

    Windows 用 `os.add_dll_directory`（在子进程里调用），Linux/macOS 用
    `LD_LIBRARY_PATH` / `DYLD_LIBRARY_PATH`——**必须在 exec 前设置**，
    进程启动后再改无效。
    """
    env = dict(os.environ)
    env[_ENV_COMPONENT_ROOT] = str(spec.component_root)
    env[_ENV_SITE_PACKAGES] = str(spec.site_packages)
    env[_ENV_ENTRY] = str(spec.entry)
    env[_ENV_ENGINE] = spec.engine

    # 原生库搜索路径：把 site-packages 及其常见子目录前置。
    lib_dirs = [
        str(spec.site_packages),
        str(spec.site_packages / "paddle" / "libs"),
        str(spec.site_packages / "numpy.libs"),
        str(spec.site_packages / "torch" / "lib"),
    ]
    existing_lib = env.get("LD_LIBRARY_PATH", "")
    env["LD_LIBRARY_PATH"] = os.pathsep.join(
        [*lib_dirs, existing_lib] if existing_lib else lib_dirs
    )
    existing_dyld = env.get("DYLD_LIBRARY_PATH", "")
    env["DYLD_LIBRARY_PATH"] = os.pathsep.join(
        [*lib_dirs, existing_dyld] if existing_dyld else lib_dirs
    )
    return env


#: CLI 里的内部 dispatch 子命令名（冻结包里用它启动 worker）。
INTERNAL_WORKER_COMMAND = "__component_worker__"


#: CLI 里启动本 worker 的内部 dispatch 命令名。
#:
#: **刻意不 import `omnicrawler.cli._main`**：那会给 `services` 引入一条
#: `services → cli` 的依赖，把架构门禁的环预算顶超（实测 66 > 65）。
#: services 层不该依赖 CLI 层。这里只是复用一个**稳定的字符串约定**，
#: 两侧用同一个值，由守卫 `test_internal_commands_are_dispatchable` 保证
#: CLI 那边确实注册了它。
INTERNAL_WORKER_COMMAND = "__component_worker__"


def _cli_internal_command() -> str:
    """CLI 里启动本 worker 的内部 dispatch 命令名。"""
    return INTERNAL_WORKER_COMMAND


def _worker_command() -> list[str]:
    """构造启动 worker 的命令。

    **冻结包里 `sys.executable` 是宿主 exe 本身**（不是 python 解释器），
    所以 `[sys.executable, "-m", "omnicrawler.services.component_worker"]`
    会变成 `omnicrawler-cli -m ...` —— CLI 不认识 `-m`，
    argparse 直接报 `invalid choice`（真实 CI 踩到，日志里只有一长串子命令列表）。

    两种形态：

    * **源码环境**：`python -m omnicrawler.services.component_worker`；
    * **冻结环境**：`<宿主 exe> __component_worker__`（CLI 里有内部 dispatch）。
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, _cli_internal_command()]
    return [sys.executable, "-m", "omnicrawler.services.component_worker"]


def run_component(
    spec: WheelPackSpec,
    payload: dict[str, Any],
    *,
    timeout: float = 300.0,
) -> dict[str, Any]:
    """在隔离子进程里运行组件入口，返回其 JSON 结果。

    协议：入口脚本从 stdin 读一个 JSON 对象，向 stdout 写一个 JSON 对象。
    与既有的 `ocr-file-v1` 保持同形（JSON-over-stdio），便于统一错误处理。

    **启动前重新校验文件哈希**：子进程一旦起来就没有拦截机会。
    """
    verify_component_files(spec.component_root)

    # **由宿主提供 worker**，不直接执行组件里的文件。
    #
    # 早先是 `python <spec.entry>` —— 要求 entry 是**可执行脚本**。
    # 而 Wheel-Pack 组件的 entry 是 `paddleocr/__init__.py` 这种**库入口**，
    # 直接跑只会触发 argparse 报错（真实 CI 踩到）。
    #
    # 让组件自带脚本是更糟的选择：recipe 来自市场，允许它下发脚本等于允许
    # 第三方代码执行（asset_trust 铁律禁止）。所以把「怎么执行」固定在宿主。
    command = _worker_command()
    try:
        completed = subprocess.run(  # noqa: S603 - 命令由本模块构造，非用户输入
            command,
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=component_runner_env(spec),
            cwd=str(spec.component_root),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ComponentRunnerError(
            f"组件 {spec.name} 执行超时（{timeout:.0f}s）"
        ) from exc

    if completed.returncode != 0:
        detail = (completed.stderr or "").strip()[-800:]
        raise ComponentRunnerError(
            f"组件 {spec.name} 退出码 {completed.returncode}：{detail}"
        )

    raw = (completed.stdout or "").strip()
    if not raw:
        raise ComponentRunnerError(f"组件 {spec.name} 没有输出结果")
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ComponentRunnerError(
            f"组件 {spec.name} 输出不是合法 JSON：{raw[:200]}"
        ) from exc
    if not isinstance(result, dict):
        raise ComponentRunnerError(f"组件 {spec.name} 输出顶层不是对象")
    return result


def describe_spec(spec: WheelPackSpec) -> str:
    """把运行规格渲染成 CLI 文本。"""
    return "\n".join(
        [
            f"组件：{spec.name} {spec.version}".rstrip(),
            f"引擎：{spec.engine}",
            f"site-packages：{spec.site_packages}",
            f"入口：{spec.entry}",
        ]
    )
