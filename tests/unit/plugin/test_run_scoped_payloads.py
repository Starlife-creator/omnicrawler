"""运行期插件载荷必须携带 `run_id`，宿主生命周期操作不带（issue #74 §6）。

锁定的性质：
- 运行期操作（`source.seed` / `fetcher.fetch` / `hook.*` …）拿到本次 run 的标识；
- `view.*` / `resource.*` / `capability.*` 这类**可能在任何 run 之外发生**的宿主操作不带；
- 调用方自己已给的 `run_id` 不被覆盖；
- 注入不改写调用方传入的 dict（无隐藏副作用）；
- `bind_run()` 之后拿到的是新 run 的标识。
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

pytest.importorskip("yaml")

from omnicrawler.plugins.plugin_subprocess_adapter import (  # noqa: E402
    SubprocessSourceAdapter,
    _SubprocessSessionHost,
)

_RUN_OPS = ("source.seed", "fetcher.fetch", "processor.process", "hook.after_run", "exporter.export")
_HOST_OPS = ("view.describe", "resource.inventory", "capability.describe")


def _host(tmp_path: Path, run_id: str = "run-1") -> _SubprocessSessionHost:
    return _SubprocessSessionHost(
        tmp_path, "c2_echo", permissions=set(), run_id=run_id, timeout_seconds=15
    )


@pytest.mark.parametrize("operation", _RUN_OPS)
def test_runtime_operations_carry_run_id(tmp_path: Path, operation: str) -> None:
    host = _host(tmp_path)

    payload = host._with_run_id(operation, {"config": {}})

    assert payload["run_id"] == "run-1"


@pytest.mark.parametrize("operation", _HOST_OPS)
def test_host_lifecycle_operations_do_not_carry_run_id(tmp_path: Path, operation: str) -> None:
    """`view.*` / `resource.*` 可能发生在任何 run 之外，带上反而是错误信号。"""
    host = _host(tmp_path)

    payload = host._with_run_id(operation, {"config": {}})

    assert "run_id" not in payload


def test_caller_provided_run_id_wins(tmp_path: Path) -> None:
    host = _host(tmp_path, run_id="run-host")

    payload = host._with_run_id("processor.process", {"run_id": "run-explicit"})

    assert payload["run_id"] == "run-explicit"


def test_injection_does_not_mutate_caller_dict(tmp_path: Path) -> None:
    host = _host(tmp_path)
    original = {"config": {}}

    host._with_run_id("source.seed", original)

    assert original == {"config": {}}  # 调用方的 dict 不被就地改


def test_empty_run_id_is_not_injected(tmp_path: Path) -> None:
    """没有 run 归属时（独立会话/测试）不应凭空造一个标识。"""
    host = _host(tmp_path, run_id="")

    assert "run_id" not in host._with_run_id("source.seed", {})


def test_bind_run_switches_identifier(tmp_path: Path) -> None:
    host = _host(tmp_path, run_id="run-1")
    host.bind_run("run-2")

    assert host._with_run_id("source.seed", {})["run_id"] == "run-2"


def test_reverse_guard_runtime_classification_is_what_matters(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """反向断言：把「宿主生命周期」判定强行改成 False（即把所有操作都当运行期），
    `view.*` 就必须被注入 run_id ⇒ 上一条性质确实由该判定提供，而不是恰好成立。"""
    import omnicrawler.plugins.plugin_subprocess_adapter as adapter

    host = _host(tmp_path)
    assert "run_id" not in host._with_run_id("view.describe", {})

    monkeypatch.setattr(adapter, "_is_host_lifecycle_operation", lambda operation: False)

    assert host._with_run_id("view.describe", {})["run_id"] == "run-1"


_ECHO_PLUGIN = '''
PLUGIN_METADATA = {"name": "c2_echo", "version": "1.0.0", "permissions": []}


def handle(operation, payload):
    if operation == "source.seed":
        return {"requests": [{
            "url": "https://example.com/echo", "method": "GET", "kind": "page",
            "meta": {"run_id": payload.get("run_id", "")},
        }]}
    return {}
'''


def test_run_id_reaches_the_plugin_end_to_end(tmp_path: Path) -> None:
    """端到端：真的经过子进程会话，插件读到的 `run_id` 与宿主绑定的一致。"""
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    # 会话按 ``<plugin_root>/<entry_module>.py`` 拉起子进程 ⇒ 文件名必须等于入口模块名
    (plugin_dir / "c2_echo.py").write_text(textwrap.dedent(_ECHO_PLUGIN), encoding="utf-8")
    root = tmp_path / "proj"
    root.mkdir()
    workspace = root / "work"
    workspace.mkdir()
    from omnicrawler.core.config import AppConfig

    config = AppConfig(
        root / "task.yaml",
        root,
        {"project": {"name": "t", "workspace": "work"}, "source": {"kind": "c2_echo"}},
        workspace,
    )
    host = _SubprocessSessionHost(
        plugin_dir,
        "c2_echo",
        permissions=set(),
        config=config,
        run_id="run-e2e",
        timeout_seconds=20,
    )

    requests = SubprocessSourceAdapter(host, config).seed()

    assert len(requests) == 1
    assert requests[0].meta["run_id"] == "run-e2e"
    host.close()
