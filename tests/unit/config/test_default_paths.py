"""S4.2：默认路径治理（project.root 固定策略 / CLI required / 启动日志）。"""

from __future__ import annotations

from pathlib import Path

from omnicrawler.cli._main import build_parser
from omnicrawler.core.config import load_config


def test_explicit_project_root_wins_over_detection(tmp_path: Path) -> None:
    root = tmp_path / "my_project"
    (root / "configs").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    config_path = root / "configs" / "task.yaml"
    config_path.write_text(
        f"project: {{name: x, workspace: work, root: '{root / 'explicit'}'}}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n",
        encoding="utf-8",
    )
    config = load_config(config_path)
    assert config.root == (root / "explicit").resolve()


def test_project_root_detection_is_deterministic(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    (root / "configs").mkdir(parents=True)
    config_path = root / "configs" / "task.yaml"
    config_path.write_text(
        "project: {name: x, workspace: work}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n",
        encoding="utf-8",
    )
    config = load_config(config_path)
    assert config.root == root.resolve()  # configs/ 目录规则


def test_cli_config_is_required_everywhere() -> None:
    parser = build_parser()
    subparsers = next(
        action
        for action in parser._actions
        if action.__class__.__name__ == "_SubParsersAction"
    )
    checked = 0
    for sub in subparsers.choices.values():
        if sub.prog == "omnicrawler plugins":
            continue  # plugins 列表命令允许不带 config
        for action in sub._actions:
            if action.dest == "config":
                assert action.required, f"{sub.prog} 的 --config 不是 required"
                checked += 1
    assert checked >= 10  # 主要命令全部 required


def test_workspace_resolves_relative_to_project_root(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    (root / "configs").mkdir(parents=True)
    config_path = root / "configs" / "task.yaml"
    config_path.write_text(
        "project: {name: x, workspace: 'data/work'}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n",
        encoding="utf-8",
    )
    config = load_config(config_path)
    assert config.workspace == (root / "data" / "work").resolve()
