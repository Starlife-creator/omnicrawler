"""W3.4：离线运行级验收（`tools/offline_acceptance.py`）的本地可验部分。

## 这里守什么

该工具的「市场安装 + 真实抓取」两段需要真实环境（市场仓 + 容器），本地只跑纯逻辑；
但下面这几条是**最容易被写坏、且写坏了 CI 会以难懂方式失败**的判据：

1. ★ **"确实断网"的证明不得空转**：只要任一外连成功，整轮检查就失去意义
   ⇒ 必须**判红**，而不是静默继续（否则绿灯的含义只是"当时网是通的"）。
2. ★ **负向对照**：断网时抓公网**必须失败**；若它"成功"（可能返回空但绿灯），必须判红。
3. ★ **接线契约**：`quality.yml` 的 docker job 必须真的用 `--network none` +
   `--require-offline` 跑这个工具，并且把市场仓只读挂进去 —— 否则退化成"跑了但没断网"。
4. 输出在 cp1252 控制台上不得崩（与归档冒烟工具同一条纪律）。
"""

from __future__ import annotations

import importlib.util
import io
import subprocess
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MODULE_PATH = _REPO_ROOT / "tools" / "offline_acceptance.py"
_QUALITY = _REPO_ROOT / ".github" / "workflows" / "quality.yml"


def _module():
    spec = importlib.util.spec_from_file_location("offline_acceptance", _MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_offline_proof_fails_when_any_probe_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 反空转：只要有外连成功，"断网"就不成立 ⇒ 必须判红。"""
    module = _module()
    monkeypatch.setattr(module, "_probe", lambda host, port: f"{host}:{port} CONNECTED")
    with pytest.raises(RuntimeError) as info:
        module.assert_offline()
    assert "NOT offline" in str(info.value)


def test_offline_proof_passes_only_when_every_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    monkeypatch.setattr(module, "_probe", lambda host, port: None)
    module.assert_offline()  # 不应抛异常


def test_negative_control_fails_when_a_public_seed_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 断网时抓公网却"成功"（哪怕产出是空）⇒ 判红，不许静默通过。"""
    module = _module()

    def _fake_run(argv, cwd, *, timeout=600):
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    monkeypatch.setattr(module, "_run", _fake_run)
    monkeypatch.setattr(module, "_records", lambda workspace: ['{"data": {"text": "x"}}'])
    with pytest.raises(RuntimeError) as info:
        module.assert_public_fetch_fails(tmp_path)
    assert "successfully" in str(info.value)


def test_negative_control_accepts_a_real_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()

    def _fake_run(argv, cwd, *, timeout=600):
        return subprocess.CompletedProcess(argv, 1, "", "network unreachable")

    monkeypatch.setattr(module, "_run", _fake_run)
    monkeypatch.setattr(module, "_records", lambda workspace: [])
    module.assert_public_fetch_fails(tmp_path)  # 不应抛异常


def test_quality_workflow_runs_the_offline_acceptance_offline() -> None:
    """★ 接线契约：必须 `--network none` + `--require-offline`，且市场仓只读挂载。"""
    data = yaml.safe_load(_QUALITY.read_text(encoding="utf-8"))
    steps = (data.get("jobs") or {}).get("docker", {}).get("steps") or []
    runs = "\n".join(str(step.get("run") or "") for step in steps)
    assert "offline_acceptance.py" in runs, "docker job 没有跑离线验收工具"
    assert "--network none" in runs, (
        "离线验收必须跑在 --network none 容器里 —— 否则它是「在线验收」，结论无意义"
    )
    assert "--require-offline" in runs, (
        "缺 --require-offline ⇒ 工具会跳过「确实断网」的证明，整轮检查可能空转"
    )
    assert "/market:ro" in runs, "市场仓必须以只读方式挂进容器（容器自身不产生网络访问）"


def test_prints_survive_a_cp1252_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """与归档冒烟工具同一条纪律：输出必须全 ASCII（Windows runner 是 cp1252）。"""
    module = _module()
    # 走**成功路径**（它会打印 "offline proven: ..."）；失败路径在打印前就抛，buffer 会是空的
    monkeypatch.setattr(module, "_probe", lambda host, port: None)
    buffer = io.BytesIO()
    console = io.TextIOWrapper(buffer, encoding="cp1252")
    original = module.sys.stdout
    module.sys.stdout = console
    try:
        module.assert_offline()
    finally:
        module.sys.stdout = original
        console.flush()
    printed = buffer.getvalue()
    assert printed, "工具没打印任何东西 ⇒ 本用例在空转"
    assert printed.decode("ascii"), "工具在 cp1252 控制台上打印了非 ASCII"
    assert b"offline proven" in printed
