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


def _utf8_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """给子进程一份**保证 UTF-8 I/O** 的环境。

    为什么必须有：本模块按 UTF-8 解子进程的输出（`encoding="utf-8"`），
    但 Windows 默认控制台/管道下，子进程的 stdout 按 locale（cp936/cp1252）
    编码。父进程按 UTF-8 解 cp936 字节，中文会变成 U+FFFD ——
    这在 `smoke.expect` 里表现为「键存在但值是乱码」，极难定位。

    **不能 `from tools.child_env import utf8_env`**：`tools/` 是开发期工具，
    不在运行时依赖里。`src/` 层不该依赖它。
    """
    env = dict(os.environ if base is None else base)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def component_runner_env(spec: WheelPackSpec) -> dict[str, str]:
    """构造子进程环境：注入组件路径，并把原生库搜索路径前置。

    Windows 用 `os.add_dll_directory`（在子进程里调用），Linux/macOS 用
    `LD_LIBRARY_PATH` / `DYLD_LIBRARY_PATH`——**必须在 exec 前设置**，
    进程启动后再改无效。
    """
    env = _utf8_env()
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
    # **不继承宿主的 LD_LIBRARY_PATH / DYLD_LIBRARY_PATH**。
    #
    # 冻结包里这两个变量指向包内 `_internal/`，那里有 PyInstaller 收集的一批
    # 系统库（如 `libsystemd.so.0`）。子进程是真正的 `python` 解释器，
    # 若继承这条路径，它启动的**任何子命令**都会先命中包内的旧版库：
    #
    #     cat: .../_internal/libsystemd.so.0: version `LIBSYSTEMD_254' not found
    #
    # 真实 CI 踩到（组件 run 阶段 exit 1）。组件子进程只该看它自己的
    # site-packages，宿主打包用的库不该泄漏进去。
    env["LD_LIBRARY_PATH"] = os.pathsep.join(lib_dirs)
    env["DYLD_LIBRARY_PATH"] = os.pathsep.join(lib_dirs)
    return env


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
    # ★ 判据不能只看 `sys.frozen`：PyInstaller 的 CLI 入口在部分打包方式下
    # 不设置它，于是会走 `-m` 分支，而宿主 exe 根本不认 `-m`
    # （argparse 报 invalid choice，子进程根本没起来 —— 真实 CI 踩到）。
    #
    # 更可靠的判据：**当前进程的 `sys.executable` 是否是一个 `omnicrawler*` 可执行
    # 文件**。源码环境下它是 python.exe/python，冻结环境下它是 omnicrawler-cli。
    executable_name = Path(sys.executable).name.lower()
    looks_frozen = getattr(sys, "frozen", False) or executable_name.startswith(
        ("omnicrawler",)
    )
    if looks_frozen:
        return [sys.executable, _cli_internal_command()]
    return [sys.executable, "-m", "omnicrawler.services.component_worker"]


def _extract_worker_error(stdout: str) -> str:
    """从 worker 的 stdout 里取出它自报的错误（没有就返回空串）。

    worker 的协议是「stdout 只放一个 JSON」。失败时那个 JSON 长这样：

        {"ok": false, "stage": "import_component", "error": "ImportError: ..."}

    `stage` 是关键 —— 它直接说明炸在 import 还是后端探针。
    """
    text = (stdout or "").strip()
    if not text:
        return ""
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return ""
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, dict) or payload.get("ok"):
        return ""
    stage = str(payload.get("stage") or "").strip()
    error = str(payload.get("error") or "").strip()
    if not stage and not error:
        return ""
    detail = f"[{stage or 'worker'}] {error or '未提供错误信息'}"
    # 有 traceback 就带上末尾几行 —— 那是定位「哪一行炸的」的关键信息。
    trace = str(payload.get("traceback") or "").strip()
    if trace:
        tail = "\n".join(
            line for line in trace.splitlines()[-8:] if line.strip()
        )
        if tail:
            detail = f"{detail}\n{tail}"
    return detail


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
        # **先看 stdout**：worker 失败时会往 stdout 打一个 JSON
        # （`{"ok": false, "stage": ..., "error": ...}`）。早先只读 stderr，
        # 于是真正的错误被 `内置 Tesseract 缺失` 这类无关告警淹没
        # （真实 CI 上"退出码 1"后面跟的全是告警，看不出失败原因）。
        reported = _extract_worker_error(completed.stdout or "")
        if reported:
            raise ComponentRunnerError(
                f"组件 {spec.name} 退出码 {completed.returncode}：{reported}"
            )
        # worker 可能已经写出了**非 error 形态**的 JSON（`ok=False` 但没带
        # error 字段，例如 paddle 探针只设了 `backend`）。此时仍然要把 JSON
        # 带出来 —— 否则调用方看到的只有退出码，拿不到任何有用信息。
        raw_stdout = (completed.stdout or "").strip()
        if raw_stdout.startswith("{"):
            snippet = raw_stdout[:800]
            raise ComponentRunnerError(
                f"组件 {spec.name} 退出码 {completed.returncode}：{snippet}"
            )
        # 没有 JSON（worker 自己崩了）：报**最后 10 行**而不是最后 800 字符 ——
        # 崩溃栈的末尾几行才是「哪一行炸的」，而中文字符占 3 字节，
        # 800 字符窗口很容易被前面的告警吃光。
        stderr_lines = [ln for ln in (completed.stderr or "").strip().splitlines() if ln.strip()]
        detail = "\n".join(stderr_lines[-10:])
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


#: native 组件的运行规格。
#:
#: 与 `WheelPackSpec` 的区别：native 组件**自带可执行文件**，不需要
#: `site-packages`，也不需要宿主 Python 运行时。它直接 exec，走与
#: wheel-pack 相同的 **JSON-over-stdio** 协议。
#:
#: 为什么要单列一个类型而不是给 `WheelPackSpec` 加可选字段：
#: `site_packages` 在 native 里根本不存在，做成可空字段会让每个使用点
#: 都要判断"这个字段现在是不是 None"。两个独立的冻结数据类更清楚。
@dataclass(frozen=True, slots=True)
class NativeSpec:
    """native 组件的运行规格（自带可执行文件，直接 exec）。"""

    name: str
    version: str
    component_root: Path
    executable: Path
    engine: str


def _is_within(path: Path, base: Path) -> bool:
    """`path` 是否落在 `base` 之内（含 base 本身）。"""
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True


def build_native_spec(component_root: Path, entry_data: dict[str, Any]) -> NativeSpec:
    """从已装配的 native 组件目录读出运行规格。

    native 组件的目录形态::

        <component_root>/
            component.json          # 清单（含 files 哈希）
            bin/<可执行文件>         # 由 recipe 的 entry.path 指向
            entry.json              # 入口声明

    **可执行位必须存在**（非 Windows）。装配时保留了文件内容与哈希，
    但 tar/zip 里的权限位不一定可靠，所以这里显式检查并给出可读的错误，
    而不是等到 exec 时才报 `Permission denied`。
    """
    root = Path(component_root)
    entry_path = str(entry_data.get("path") or "").strip()
    if not entry_path:
        raise ComponentRunnerError(f"native 组件的 entry 缺少 path：{root}")

    executable = (root / entry_path).resolve()
    # 防目录穿越：入口必须落在组件目录内。
    #
    # **唯一豁免**：目录型内置组件（浏览器内核）的字节留在安装根，
    # 不复制进组件目录 —— 它的 `component.json` 用 `external_root`
    # 显式声明真实位置。豁免仍受约束：只能落在 `external_root` 或
    # 安装根之内，不能指向任意路径。
    allowed_roots = [root.resolve()]
    manifest_path = root / "component.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = {}
        external = str(manifest.get("external_root") or "").strip()
        if external:
            allowed_roots.append(Path(external).resolve())
            from ..core.runtime_paths import install_root

            allowed_roots.append(Path(install_root()).resolve())

    if not any(_is_within(executable, base) for base in allowed_roots):
        raise ComponentRunnerError(
            f"native 组件的 entry.path 逃出了组件目录：{entry_path}"
        )

    if not executable.is_file():
        raise ComponentRunnerError(
            f"native 组件的可执行文件不存在：{executable}（entry.path={entry_path}）"
        )

    if os.name != "nt" and not os.access(executable, os.X_OK):
        raise ComponentRunnerError(
            f"native 组件的可执行文件没有执行权限：{executable}"
            "（装配应保留可执行位；若是 tar/zip 解压导致丢失，需要 chmod +x）"
        )

    return NativeSpec(
        name=str(entry_data.get("name") or root.name),
        version=str(entry_data.get("version") or ""),
        component_root=root,
        executable=executable,
        engine=str(entry_data.get("engine") or "native"),
    )


def run_native_component(
    spec: NativeSpec,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 300.0,
) -> dict[str, Any]:
    """在子进程里 exec native 组件，返回其 JSON 结果。

    协议与 `run_component` 一致：**stdin 一个 JSON 对象，stdout 一个 JSON 对象**。
    这让 `smoke.expect` 的判据可以对两种 kind 通用，不必各写一套。

    **为什么仍然要 exec 而不是让宿主调用它的库**：native 组件的定义就是
    「独立可执行文件」。宿主不链接它的任何符号，因此不存在符号冲突。
    """
    verify_component_files(spec.component_root)

    command = [str(spec.executable)]
    try:
        completed = subprocess.run(  # noqa: S603 - 路径来自已校验的组件目录
            command,
            input=json.dumps(payload or {}, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(spec.component_root),
            env=_utf8_env(),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ComponentRunnerError(
            f"组件 {spec.name} 执行超时（{timeout:.0f}s）"
        ) from exc
    except OSError as exc:
        raise ComponentRunnerError(
            f"组件 {spec.name} 无法执行：{type(exc).__name__}: {exc}"
        ) from exc

    raw = (completed.stdout or "").strip()
    if completed.returncode != 0 and not raw:
        stderr_lines = [
            ln for ln in (completed.stderr or "").strip().splitlines() if ln.strip()
        ]
        detail = "\n".join(stderr_lines[-10:]) or "（无输出）"
        raise ComponentRunnerError(
            f"组件 {spec.name} 退出码 {completed.returncode}：{detail}"
        )
    if not raw:
        raise ComponentRunnerError(f"组件 {spec.name} 没有输出结果")

    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ComponentRunnerError(
            f"组件 {spec.name} 输出不是 JSON：{raw[:200]}"
        )
    try:
        result = json.loads(raw[start : end + 1])
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
