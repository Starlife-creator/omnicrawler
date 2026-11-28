"""`diagnose_from_state` 此前**永远返回空列表** —— 它调的方法不存在。

## 洞在哪（2026-11-28）

`quality/diagnostics.py` 里那份分类规则写得很全（网络瞬断 / 限流 / 选择器失效 /
登录失效 / 浏览器不可用 / 编码问题…），但 `diagnose_from_state()`：

```python
try:
    raw_errors = state_store.error_summary(run_id)   # ← StateStore 没有这个方法
except Exception:
    return []                                       # ← 永远走这里
```

两层问题叠在一起：

1. **`StateStore` 从来没有 `error_summary`** —— 它只有 `add_error` / `stats` / …；
2. 调用被 `except Exception` 包着 ⇒ **永远返回 `[]`**，而 `[]` 与「没有错误」
   长得一模一样；
3. 而它同时**零调用方**，所以坏了也没人发现。

## 修法

- `StateStore.error_messages(run_id, limit=200)` —— 真正读 `errors` 表，
  返回 `"{stage}: {error_type}: {message}"`（带上 stage/type，诊断正则才有线索）；
- `diagnose_from_state` 改调它；`except` 收窄到 `AttributeError` / `OSError`
  并**显式警告**（接口不符是编程错误，不该被吞掉）；
- 接进 `omnicrawler status`：失败运行才做诊断（成功运行结果是空列表，不值得多查一次库）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from omnicrawler.core.models import CrawlRequest
from omnicrawler.quality.diagnostics import diagnose_from_state
from omnicrawler.state import StateStore


def _store(tmp_path: Path, errors: list[tuple[str, str, Exception]]) -> tuple[StateStore, str]:
    store = StateStore(tmp_path / "state.sqlite3")
    run_id = store.start_run("demo", str(tmp_path / "c.yaml"))
    for stage, url, exc in errors:
        store.add_error(run_id, CrawlRequest(url), stage, exc)
    return store, run_id


# ── StateStore.error_messages ────────────────────────────────────────────


def test_error_messages_reads_registered_errors(tmp_path: Path) -> None:
    store, run_id = _store(tmp_path, [("fetch", "https://a/", TimeoutError("timed out"))])
    try:
        messages = store.error_messages(run_id)
    finally:
        store.close()
    assert len(messages) == 1
    assert "TimeoutError" in messages[0]
    assert "fetch" in messages[0], "诊断正则需要 stage 线索"


def test_error_messages_is_empty_for_unknown_run(tmp_path: Path) -> None:
    store, _ = _store(tmp_path, [("fetch", "https://a/", TimeoutError("x"))])
    try:
        assert store.error_messages("no-such-run") == []
    finally:
        store.close()


def test_error_messages_returns_newest_first(tmp_path: Path) -> None:
    store, run_id = _store(tmp_path, [
        ("fetch", "https://a/", TimeoutError("first")),
        ("extract", "https://b/", ValueError("second")),
    ])
    try:
        messages = store.error_messages(run_id)
    finally:
        store.close()
    assert "second" in messages[0], f"最新错误应在前：{messages}"


def test_error_messages_limit_is_bounded(tmp_path: Path) -> None:
    store, run_id = _store(tmp_path, [
        ("fetch", f"https://a/{i}", TimeoutError(f"e{i}")) for i in range(5)
    ])
    try:
        assert len(store.error_messages(run_id, limit=2)) == 2
        # 上限截断：过大值不炸，也不返回超过上限
        assert len(store.error_messages(run_id, limit=10**9)) <= 5
    finally:
        store.close()


# ── diagnose_from_state ────────────────────────────────────────────────


def test_diagnose_from_state_is_not_empty_when_errors_exist(tmp_path: Path) -> None:
    """★ 核心判据：有错误就必须有诊断。修前这里恒为 `[]`。"""
    store, run_id = _store(tmp_path, [("fetch", "https://a/", TimeoutError("timed out"))])
    try:
        reports = diagnose_from_state(run_id, store)
    finally:
        store.close()
    assert reports, "有错误却返回空诊断 —— `error_summary` 那条老路又回来了"
    assert reports[0].action.strip(), "诊断必须给出可执行的下一步"


def test_diagnose_from_state_classifies_network_errors(tmp_path: Path) -> None:
    store, run_id = _store(tmp_path, [("fetch", "https://a/", TimeoutError("timed out"))])
    try:
        reports = diagnose_from_state(run_id, store)
    finally:
        store.close()
    assert reports[0].category.value == "network_transient"
    assert reports[0].retryable is True


def test_diagnose_from_state_empty_when_no_errors(tmp_path: Path) -> None:
    """★ 反方向：「没有错误」必须是真空，不能因为修法而编出诊断。"""
    store, run_id = _store(tmp_path, [])
    try:
        assert diagnose_from_state(run_id, store) == []
    finally:
        store.close()


def test_missing_method_is_reported_not_silently_swallowed(tmp_path: Path, caplog) -> None:
    """★ 接口不符时必须**留下警告** —— 那正是修前被吞掉的东西。"""
    import logging

    class Broken:
        pass

    with caplog.at_level(logging.WARNING):
        cheap = diagnose_from_state("run-1", Broken())
    assert cheap == []
    assert any("读取运行错误失败" in r.message for r in caplog.records), (
        "传入不提供 error_messages 的对象时必须告警，不能静默返回 []"
    )


# ── 接进 `status` ──────────────────────────────────────────────────────


def test_status_reports_diagnostics_for_a_failed_run(tmp_path: Path) -> None:
    """★ 端到端：失败运行的 `status` 输出里必须有 `diagnostics` 段。"""
    workspace = tmp_path / "work"
    workspace.mkdir(parents=True)
    config = tmp_path / "c.yaml"
    config.write_text(
        f"config_version: 3\nproject:\n  name: demo\n  workspace: {workspace}\n"
        "source:\n  kind: static_html\n  seeds:\n    - https://example.com/\n"
        "crawl:\n  max_pages: 1\n",
        encoding="utf-8",
    )
    store = StateStore(workspace / "state.sqlite3")
    run_id = store.start_run("demo", str(config))
    store.add_error(run_id, CrawlRequest("https://example.com/a"), "fetch", TimeoutError("timed out"))
    store.close()

    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(
        [sys.executable, "-m", "omnicrawler", "status", "-c", str(config)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    assert result.returncode == 0, result.stderr[-400:]
    payload = json.loads(result.stdout)
    assert payload.get("diagnostics"), f"失败运行没有诊断：{sorted(payload)}"
    first = payload["diagnostics"][0]
    assert set(first) >= {"category", "cause", "action", "data_impact", "retryable"}


def test_status_is_silent_when_run_succeeded(tmp_path: Path) -> None:
    """★ 反向：成功运行**不许多出一段空诊断**（那是噪音，也白查一次库）。"""
    workspace = tmp_path / "work"
    workspace.mkdir(parents=True)
    config = tmp_path / "c.yaml"
    config.write_text(
        f"config_version: 3\nproject:\n  name: demo\n  workspace: {workspace}\n"
        "source:\n  kind: static_html\n  seeds:\n    - https://example.com/\n"
        "crawl:\n  max_pages: 1\n",
        encoding="utf-8",
    )
    store = StateStore(workspace / "state.sqlite3")
    run_id = store.start_run("demo", str(config))
    store.finish_run(run_id, "succeeded", {})
    store.close()

    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(
        [sys.executable, "-m", "omnicrawler", "status", "-c", str(config)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    payload = json.loads(result.stdout)
    assert "diagnostics" not in payload
