"""Manifest validation and fail-closed subprocess boundary for local plugins."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ALLOWED_PERMISSIONS = {"records:read", "records:write", "artifacts:read", "network:scoped", "temp:write"}


@dataclass(frozen=True, slots=True)
class PluginPackageManifest:
    plugin_id: str
    version: str
    publisher: str
    compatible_core: str
    permissions: tuple[str, ...]
    signature: str

    def validate(self, approved_permissions: set[str]) -> None:
        if not self.plugin_id or not self.version or not self.publisher or not self.signature:
            raise ValueError("插件必须包含ID、版本、发布者和签名")
        unknown = set(self.permissions) - ALLOWED_PERMISSIONS
        if unknown:
            raise ValueError(f"插件声明未知权限: {sorted(unknown)}")
        missing = set(self.permissions) - approved_permissions
        if missing:
            raise PermissionError(f"插件权限尚未批准: {sorted(missing)}")


class IsolatedPluginRunner:
    def __init__(self, plugin_root: Path, *, timeout_seconds: float = 30.0) -> None:
        self.plugin_root = plugin_root.resolve()
        self.timeout_seconds = max(0.1, timeout_seconds)

    def call(self, entry_module: str, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.plugin_root.is_dir() or any(part in {"..", ""} for part in entry_module.split(".")):
            raise ValueError("插件路径或入口无效")
        # B01-016：`-I`（isolated mode）隐含 `-E`，会忽略所有 PYTHON* 环境变量，
        # 故 PYTHONPATH/PYTHONIOENCODING/PYTHONHASHSEED 三行均为死配置——
        # 删除以避免"自以为已配置"的假象。子进程侧显式 reconfigure stdout 编码。
        env = {"OMNICRAWL_PLUGIN_SANDBOX": "1"}
        # Windows 上解释器初始化依赖 SystemRoot；env 全量替换会丢它，
        # 历史上引发 _Py_HashRandomization_Init 偶发失败（Q10 候选根因）。继承保留。
        for key in ("SystemRoot", "SYSTEMROOT", "TEMP", "TMP"):
            if key in os.environ:
                env[key] = os.environ[key]
        host = Path(__file__).with_name("plugin_subprocess.py").resolve()
        command = [sys.executable, "-I", str(host), entry_module, str(self.plugin_root)]
        request = json.dumps({"operation": operation, "payload": payload}, ensure_ascii=False)
        try:
            completed = subprocess.run(
                command, input=request, text=True, encoding="utf-8", capture_output=True,
                cwd=self.plugin_root, env=env, timeout=self.timeout_seconds, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("插件超过运行时间限制，已终止；主任务状态未改变") from exc
        if completed.returncode != 0:
            raise RuntimeError(f"插件进程失败，主任务可继续: {completed.stderr[-500:]}")
        value = json.loads(completed.stdout)
        if not isinstance(value, dict):
            raise RuntimeError("插件返回值必须是对象")
        return value
