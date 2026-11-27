"""`sign_plugin` 写透明日志必须**跟随现有格式**（2026-11-27）。

## 事故形态

市场仓的 `signing_transparency.jsonl` 早已是 **v2 哈希链**，而 `sign_plugin.py` 的
`_append_transparency_log` 一直写 **v1 扁平条目**。于是每跑一次 `sign`，就往 v2 链尾
追加一条 v1 —— 触发市场仓 CI 的「第 N 行 v1 旧条目混在 v2 哈希链之后（schema 混存）」。

实测该违规在 `138403c` 上已有 7 条：**旧工具与新日志格式不同步**，不是某一次疏忽。

## 这些用例守什么

判据是「**写进去的条目能被链校验器接受**」，而不是「函数没抛异常」：

* 空日志 ⇒ 第一条 v2 的 `seq=0`、`prev_hash=None`；
* 接着写 ⇒ `seq` 递增、`prev_hash` 等于上一条 `entry_hash`、`entry_hash` 可复算；
* 路径字段归一成 POSIX（日志纪律要求，绝不写反斜杠）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
MARKET = REPO.parent / "OmniCrawler-market"

pytestmark = pytest.mark.skipif(
    not (MARKET / "tools" / "catalog_lib").is_dir(),
    reason="市场仓不在同级目录：链校验器不可用（本用例只在双仓工作区有意义）",
)


def _load(module_path: Path, name: str):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _writer():
    return _load(REPO / "tools" / "signing" / "sign_plugin.py", "sign_plugin_under_test")


def _checker():
    sys.path.insert(0, str(MARKET / "tools"))
    try:
        from catalog_lib.transparency import (
            check_transparency_log,
            verify_transparency_chain,
        )
    finally:
        sys.path.pop(0)
    return check_transparency_log, verify_transparency_chain


def test_first_entry_is_v2_with_seq_zero(tmp_path: Path) -> None:
    writer = _writer()
    log = tmp_path / "log.jsonl"
    writer._append_transparency_log(Path("README.md"), log)
    entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 1
    assert entries[0]["schema_version"] == 2, "写成了 v1 —— 会与 v2 链混存"
    assert entries[0]["seq"] == 0 and entries[0]["prev_hash"] is None


def test_entries_link_into_a_valid_chain(tmp_path: Path) -> None:
    """★ 核心判据：写两条之后，**市场仓的链校验器必须接受**。"""
    writer = _writer()
    check, verify = _checker()
    log = tmp_path / "log.jsonl"
    writer._append_transparency_log(Path("README.md"), log)
    writer._append_transparency_log(Path("README.md"), log)
    entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [entry["seq"] for entry in entries] == [0, 1]
    assert entries[1]["prev_hash"] == entries[0]["entry_hash"]
    assert check(log) == [] and verify(log) == [], (check(log), verify(log))


def test_append_after_existing_v2_entry_continues_the_chain(tmp_path: Path) -> None:
    """日志里**已有** v2 条目时，新条目必须接着链（不是重新起链）。"""
    writer = _writer()
    check, verify = _checker()
    log = tmp_path / "log.jsonl"
    writer._append_transparency_log(Path("README.md"), log)
    first = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    writer._append_transparency_log(Path("README.md"), log)
    entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert entries[1]["seq"] == first["seq"] + 1
    assert entries[1]["prev_hash"] == first["entry_hash"]
    assert check(log) == [] and verify(log) == []


def test_path_field_has_no_backslash(tmp_path: Path) -> None:
    """日志纪律：路径字段一律 POSIX（P3-11 路径归一）。"""
    writer = _writer()
    log = tmp_path / "log.jsonl"
    # `_append_transparency_log` 会读该文件算摘要 ⇒ 必须在临时目录里真实存在。
    target_dir = tmp_path / "sub"
    target_dir.mkdir()
    target = target_dir / "plugin.py"
    target.write_text("print(1)\n", encoding="utf-8")
    writer._append_transparency_log(target, log)
    entry = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    for field in ("plugin", "package_manifest_file", "creator_identity_file"):
        value = entry.get(field)
        if isinstance(value, str):
            assert "\\" not in value, (field, value)
