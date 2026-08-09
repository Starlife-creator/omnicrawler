"""S1.5.2 消费方测试：Pipeline 构造回滚 + close 异常聚合。

验证：任一子系统构造失败时，已建资源全部释放；close 阶段单项失败
不中断整体，且错误被聚合为 RuntimeError 抛出。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from omnicrawl.core.config import load_config
from omnicrawl.pipeline import Pipeline


def _config(tmp_path: Path) -> object:
    workspace = tmp_path / "work"
    path = tmp_path / "project.yaml"
    path.write_text(yaml.safe_dump({
        "project": {"name": "rollback", "workspace": str(workspace)},
        "source": {"kind": "crawl", "seeds": ["http://127.0.0.1:1/x"]},
        "crawl": {"max_pages": 1, "max_depth": 0, "same_host": True, "concurrency": 1},
        "http": {
            "user_agent": "RollbackTest/1.0 (+contact: test@example.org)",
            "respect_robots": False, "delay_seconds": 0, "allow_private_network": True,
        },
    }, sort_keys=False), encoding="utf-8")
    return load_config(path)


class _CloseTracker:
    def __init__(self, name: str, closed: list[str]) -> None:
        self.name = name
        self.closed = closed

    def close(self) -> None:
        self.closed.append(self.name)


def test_s152_init_failure_closes_built_resources(monkeypatch, tmp_path: Path) -> None:
    """S1.5.2：构造中途失败时，已建子系统全部关闭（后建先关）。"""
    import omnicrawl.pipeline.core as core

    closed: list[str] = []
    monkeypatch.setattr(core, "StateStore", lambda _path: _CloseTracker("state", closed))
    monkeypatch.setattr(core, "build_object_store", lambda *_a, **_k: _CloseTracker("object_store", closed))
    monkeypatch.setattr(core, "build_record_sink_manager", lambda *_a, **_k: _CloseTracker("sinks", closed))
    monkeypatch.setattr(core, "RegressionLibrary", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("broken dep")))

    with pytest.raises(RuntimeError, match="broken dep"):
        Pipeline(_config(tmp_path))

    assert closed == ["sinks", "object_store", "state"], (
        "ExitStack 应按逆序回滚所有已建资源，实际: " + str(closed)
    )


def test_s152_close_aggregates_errors_and_continues(monkeypatch, tmp_path: Path) -> None:
    """S1.5.2：close 阶段单项抛异常仍继续清理其余资源，并聚合为 RuntimeError。"""
    pipeline = Pipeline(_config(tmp_path))
    try:
        closed: list[str] = []
        pipeline._shared_fetchers["broken"] = _CloseTracker("fetcher", closed)

        def _boom() -> None:
            raise RuntimeError("sink close failed")

        class _BrokenSinks(_CloseTracker):
            def close(self) -> None:
                _boom()

        pipeline.record_sinks = _BrokenSinks("sinks", closed)
        monkeypatch.setattr(pipeline.state, "close", lambda: closed.append("state"))

        with pytest.raises(RuntimeError) as excinfo:
            pipeline.close()
        message = str(excinfo.value)
        assert "sink close failed" in message
        assert "state" in closed, "state 在 sink 失败后仍应被关闭"
        # close 幂等：state 显式关闭 + ExitStack 二次关闭（StateStore.close 已防护）
        assert closed.count("state") == 2
    finally:
        try:
            pipeline.close()  # 第二次 close：sink 仍坏 → 依旧聚合异常；防止误报需捕获
        except RuntimeError:
            pass


def test_s152_close_success_raises_nothing(tmp_path: Path) -> None:
    """S1.5.2：全部资源正常关闭时 close() 不抛异常。"""
    pipeline = Pipeline(_config(tmp_path))
    pipeline.close()  # 正常路径
    pipeline.close()  # 幂等路径（executor 已置空、state 已防护）
