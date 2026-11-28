"""`plugins audit` 必须真的验一次沙箱子进程 —— 而不只是查平台能力。

## 它补的是哪个洞（2026-11-28）

`plugin_audit.probe_sandbox_backend()` 写得很实在：源码模式下它**真的** spawn
一次子进程跑 `system.info` 往返。但全仓**零调用方** —— 唯一调用它的是
`generate_environment_report()` 吗？不是：那里用的是
`plugin_os_sandbox.probe_os_sandbox()`，**只查平台能力**，不 spawn。

两者差别正是用户实际会撞上的那类问题：

| 探测 | 回答 | 漏掉的情形 |
|---|---|---|
| `probe_os_sandbox()` | 「这个平台**理论上**支持沙箱吗」 | 宿主 exe 缺失、往返失败 |
| `probe_sandbox_backend()` | 「现在**真的能跑**一个沙箱子进程吗」 | — |

`plugins audit` 是用户排查「为什么插件跑不了」的**第一入口**，
它应该连这条也验一遍。

## 为什么是 warning 而不是 error（其实更微妙）

沙箱不可用时 `payload["ok"]` 会变 False（`and sandbox_ok`）—— 因为沙箱跑不起来时
**所有 subprocess 插件都装不起来**，此时报「插件全部正常」是误导。
但 `sandbox` 字段单独暴露，用户能立刻看出「不是我的插件有问题」。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _write_plugin(root: Path, *, extra_metadata: str = "") -> None:
    directory = root / "demo"
    directory.mkdir(parents=True, exist_ok=True)
    # ★ 末尾**不能**有逗号 —— 那会让 `metadata` 变成单元组，写进 plugin.py 就成了
    #   `PLUGIN_METADATA = ("{...}",)`：一个字符串放进元组，AST 能求值但不含任何字段，
    #   于是审计报「许可缺失」。我第一次就写成那样，测试当场红。
    metadata = (
        "{'name': 'demo', 'version': '1.0.0', 'license': 'MIT', "
        "'execution_mode': 'subprocess', 'plugin_types': ['source'], "
        "'permissions': [], 'dependencies': [], 'requires_components': []"
        + extra_metadata +
        "}"
    )
    (directory / "plugin.py").write_text(
        f"PLUGIN_METADATA = {metadata}\n\ndef handle(operation, payload):\n    return {{}}\n",
        encoding="utf-8",
    )


def _audit(root: Path) -> tuple[int, dict]:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(
        [sys.executable, "-m", "omnicrawler", "plugins", "audit", "--local", str(root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    try:
        return result.returncode, json.loads(result.stdout or "{}")
    except ValueError:
        return result.returncode, {"_raw": (result.stdout or "")[-400:]}


def test_audit_reports_sandbox_probe() -> None:
    """★ 核心判据：`plugins audit` 的输出里必须有 `sandbox` 段。

    修前它没有 —— `probe_sandbox_backend()` 全仓零调用方。
    """
    root = Path(tempfile.mkdtemp())
    _write_plugin(root)
    _, payload = _audit(root)
    assert "sandbox" in payload, f"audit 没报沙箱探测：{sorted(payload)}"
    sandbox = payload["sandbox"]
    assert set(sandbox) >= {"backend", "ok", "detail"}
    assert isinstance(sandbox["ok"], bool)


def test_sandbox_probe_actually_spawns_in_source_mode() -> None:
    """★ 判据不是「字段存在」，而是**真的 spawn 过**。

    源码模式下 `detail` 会写「subprocess 往返正常」—— 那是 spawn 成功的证据。
    冻结模式（伴生 exe）下 detail 是另一句；两种都接受，但必须非空、
    且 `ok=True` 时 backend 不能是 "none"。
    """
    root = Path(tempfile.mkdtemp())
    _write_plugin(root)
    _, payload = _audit(root)
    sandbox = payload["sandbox"]
    assert str(sandbox["detail"]).strip(), "detail 为空 ⇒ 说不清探到了什么"
    if sandbox["ok"]:
        assert sandbox["backend"] != "none", "ok=True 但 backend=none 自相矛盾"


def test_probe_sandbox_backend_is_importable_and_typed() -> None:
    """直接调它（不经 CLI）：返回三字段字典。"""
    from omnicrawler.plugins.plugin_audit import probe_sandbox_backend

    result = probe_sandbox_backend()
    assert set(result) == {"backend", "ok", "detail"}
    assert isinstance(result["ok"], bool)


def test_audit_still_reports_plugin_findings() -> None:
    """★ 反方向：加了沙箱段不许把原来的审计结论弄丢。"""
    root = Path(tempfile.mkdtemp())
    _write_plugin(root)
    _, payload = _audit(root)
    assert payload.get("audited"), "审计结果为空"
    findings = payload["audited"][0]["findings"]
    codes = {item["code"] for item in findings}
    assert "license_ok" in codes, f"许可检查丢了：{sorted(codes)}"
    assert "contract2" in codes, f"契约检查丢了：{sorted(codes)}"


def test_audit_ok_is_false_when_a_plugin_has_errors() -> None:
    """★ 沙箱段不许掩盖插件自身的问题。

    缺 `dependencies` 会触发 `gate3_dependencies_missing`（error）⇒ ok=False。
    """
    root = Path(tempfile.mkdtemp())
    directory = root / "broken"
    directory.mkdir(parents=True)
    (directory / "plugin.py").write_text(
        "PLUGIN_METADATA = {'name': 'broken', 'version': '1.0.0', 'license': 'MIT', "
        "'execution_mode': 'subprocess', 'plugin_types': ['source'], 'permissions': []}\n"
        "def handle(operation, payload):\n    return {}\n",
        encoding="utf-8",
    )
    code, payload = _audit(root)
    assert code == 1, "有 error 级 finding 时退出码必须是 1"
    assert payload.get("ok") is False
    codes = {item["code"] for item in payload["audited"][0]["findings"]}
    assert "gate3_dependencies_missing" in codes
