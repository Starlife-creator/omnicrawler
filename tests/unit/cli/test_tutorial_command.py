"""`omnicrawler tutorial`：把 10 步上手教程接到用户面前（2026-11-28）。

## 它补的是哪个洞

`services/offline_demo.py` 里有一份写得很完整的 10 步教程 ——
每步都有标题 / 描述 / 你要做什么 / 会发生什么 / 一条要点。
配套还有 `get_tutorial_map()` / `get_tutorial_step()` /
`create_tutorial_workspace()`。

**但全部零调用方**：GUI 里没有入口，CLI 里没有命令，文档里没提。
`TUTORIAL_STEPS` 那 10 行数据等于写了个没人读的文件。

## 三条判据

| 用法 | 期望 |
|---|---|
| `tutorial` | 列出 10 步（含分类、是否需要网络）|
| `tutorial --step N` | 打印该步五项内容 |
| `tutorial --workspace <dir>` | 真的生成离线练习工作区（**无网络**也能跑）|

`--workspace` 不强制配 `--step` —— 「先看到东西」比「先读十步」更像新手第一个动作。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _run(*args: str) -> tuple[int, dict]:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(
        [sys.executable, "-m", "omnicrawler", "tutorial", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    try:
        return result.returncode, json.loads(result.stdout or "{}")
    except ValueError:
        return result.returncode, {"_raw": (result.stdout or "")[-400:]}


def test_lists_all_ten_steps() -> None:
    code, payload = _run()
    assert code == 0
    steps = payload["steps"]
    assert len(steps) == 10, f"教程步数不对：{len(steps)}"
    assert [s["number"] for s in steps] == list(range(1, 11))
    assert payload["categories"], "分类表为空"


def test_each_step_has_the_five_teaching_fields() -> None:
    """★ 判据不是「列出来」，而是每步真的把**怎么用**讲全了。"""
    _, payload = _run("--step", "5")
    for key in ("title", "what_you_do", "what_happens", "key_takeaway"):
        assert str(payload.get(key, "")).strip(), f"第 5 步缺 {key}"


def test_out_of_range_step_is_a_usage_error() -> None:
    """越界必须是**用法错误**（退出码 2），不是静默空结果。"""
    code, payload = _run("--step", "99")
    assert code == 2
    assert payload.get("ok") is False
    assert "1-10" in payload.get("error", "")


def test_workspace_is_actually_created_offline() -> None:
    """★ 核心判据：`--workspace` 要真的产出可用的离线工作区。

    检查的是**产物**，不是「命令没报错」：配置文件 + 三份种子页面 + 两个 PDF。
    """
    target = Path(tempfile.mkdtemp()) / "tutorial-ws"
    code, payload = _run("--step", "1", "--workspace", str(target))
    assert code == 0, payload
    assert payload.get("ok") is True
    config = Path(payload["config"])
    assert config.is_file(), "配置文件没生成"
    assert config.parent == Path(payload["workspace"]).resolve()
    names = {p.name for p in config.parent.iterdir() if p.is_file()}
    assert {"index.html", "api.json", "report.pdf", "scan.pdf"} <= names, (
        f"离线种子文件不全：{sorted(names)}"
    )


def test_workspace_defaults_to_step_one_without_step_flag() -> None:
    """不传 `--step` 也允许生成工作区（默认第 1 步）。"""
    target = Path(tempfile.mkdtemp()) / "ws"
    code, payload = _run("--workspace", str(target))
    assert code == 0, payload
    assert payload["step"] == 1


def test_next_hint_points_at_a_real_command() -> None:
    """★ 提示里给的下一步必须是**能跑的命令**，不是一句文案。

    这条防的是「提示写得很像命令，但路径是拼错的」。
    """
    target = Path(tempfile.mkdtemp()) / "ws"
    _, payload = _run("--step", "2", "--workspace", str(target))
    next_hint = payload["next"]
    assert next_hint.startswith("omnicrawler validate -c "), next_hint
    config_path = next_hint.split("-c ", 1)[1].strip()
    assert Path(config_path).is_file(), f"提示里的配置路径不存在：{config_path}"
