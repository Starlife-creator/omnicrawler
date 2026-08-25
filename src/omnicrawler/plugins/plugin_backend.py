"""插件子进程后端解析器（Phase 2a C1：单一生产后端 + 开发回退）。

生产路径（唯一）：``omnicrawler-sandbox-host.exe`` —— PyInstaller onefile，
仅宿主脚本（plugin_subprocess.py）+ 标准库，**不打包 omnicrawler 与任何宿主
依赖**；隔离由 bundle 构成 + 子进程导入隔离构成。模式同 omnicrawler-worker.exe。
（FINAL-S1 口径：OS 级 confinement 未接线，见 resolve_backend_command 注。）

开发路径（源码模式）：``[sys.executable, "-I", "-S", plugin_subprocess.py]``
——实测 ``-I -S`` 后 sys.path 仅剩标准库（``-I`` 仅隐含 ``-E -s``，不禁
site-packages；``-S`` 才真正切断 site 注入，故两者必须并用）。

两后端跑同一套契约与穿透用例（CI 矩阵），portable_smoke_test 断言宿主 exe
内 ``import omnicrawler`` 必失败。
"""

from __future__ import annotations

import sys
from pathlib import Path

from ..core.runtime_paths import application_dir, is_frozen

HOST_EXE_NAME = "omnicrawler-sandbox-host"
_HOST_SCRIPT = "plugin_subprocess.py"

# 冻结冷启动（onefile 自解压）握手放宽 60s；源码模式 30s（SLO 预算内）
HANDSHAKE_TIMEOUT_FROZEN = 60.0
HANDSHAKE_TIMEOUT_SOURCE = 30.0


def _host_script_path() -> Path:
    """开发模式下宿主脚本的绝对路径（与 plugin_backend.py 同目录）。"""
    return Path(__file__).with_name(_HOST_SCRIPT).resolve()


def bundled_sandbox_host() -> Path | None:
    """冻结产物目录内的宿主 exe（不存在返回 None，由 audit 校验）。"""
    if not is_frozen():
        return None
    exe = HOST_EXE_NAME + (".exe" if sys.platform == "win32" else "")
    candidate = application_dir() / exe
    return candidate if candidate.is_file() else None


def resolve_backend_command() -> tuple[list[str], float]:
    """返回 (启动命令 argv, 握手超时秒数)。

    - 冻结模式：优先伴生宿主 exe；缺失时 fail-closed 抛错（由调用方映射为
      E_UNSUPPORTED_ENV 拒载语义——沙箱后端不可用不静默降级）。
    - 源码模式：``[sys.executable, -I, -S, plugin_subprocess.py]``。

    NEW-A 契约：OS 级 confinement 缺失即视为 fail-closed 拒载。当前实现仅具备
    进程边界 + 导入隔离 +（Unix）resource 限额；Windows 无 `resource` 模块且未接
    Job Object 时，仍依赖导入隔离 + 冻结宿主，并在子进程入口显式降级记录——不将
    "缺 OS confinement" 静默视为完整沙箱。
    """
    if is_frozen():
        host = bundled_sandbox_host()
        if host is None:
            raise FileNotFoundError(
                f"沙箱宿主 {HOST_EXE_NAME} 缺失（冻结产物不完整，拒绝以宿主解释器回退）"
            )
        return [str(host)], HANDSHAKE_TIMEOUT_FROZEN
    return [sys.executable, "-I", "-S", str(_host_script_path())], HANDSHAKE_TIMEOUT_SOURCE


def backend_name() -> str:
    """当前后端标识（runtime_backend 三态之 subprocess 子项，供审计/诊断）。"""
    return "frozen_host" if is_frozen() else "source_isolated"
