"""S4.3.1：破坏性命令统一防护（--apply 才执行）。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from omnicrawl.cli._main import build_parser
from omnicrawl.core.safe_action import (
    ConfirmationRequiredError,
    require_explicit_apply,
    require_known_stage,
)


def test_require_explicit_apply_blocks_without_flag() -> None:
    with pytest.raises(ConfirmationRequiredError, match="--apply"):
        require_explicit_apply("workspace rollback", argv=["workspace", "rollback"])
    require_explicit_apply("workspace rollback", argv=["workspace", "rollback", "--apply"])
    require_explicit_apply("uninstall", argv=["components", "uninstall", "--yes"])


def test_require_known_stage_rejects_unknown() -> None:
    require_known_stage("extract", {"ingest", "extract"})
    with pytest.raises(ValueError, match="未知阶段"):
        require_known_stage("mystery", {"ingest", "extract"})


def test_parser_accepts_global_apply_flag() -> None:
    parser = build_parser()
    args = parser.parse_args(["workspace", "rollback", "--config", "x.yaml", "--apply"])
    assert args.apply is True


@pytest.mark.parametrize(
    ("command", "args", "requires_apply"),
    [
        (["workspace", "rollback"], ["--config", "task.yaml"], True),
        (["workspace", "health"], ["--config", "task.yaml"], False),
        (["recovery", "rollback-config"], ["--config", "task.yaml"], True),
        (["recovery", "continue"], ["--config", "task.yaml"], False),
        (["components", "uninstall"], ["--name", "ocr"], True),
        (["components", "list"], [], False),
    ],
)
def test_handlers_enforce_apply(monkeypatch, command, args, requires_apply) -> None:
    from omnicrawl.cli import _handlers as handlers

    config_path = Path("task.yaml")
    monkeypatch.setattr(handlers, "load_config", lambda _p: SimpleNamespace(path=config_path))
    monkeypatch.setattr(handlers, "cmd_workspace", SimpleNamespace(execute=lambda *a, **k: {}))
    monkeypatch.setattr(handlers, "cmd_recovery", SimpleNamespace(execute=lambda *a, **k: {}))
    monkeypatch.setattr(handlers, "cmd_components", SimpleNamespace(execute=lambda *a, **k: {}))

    name = command[0]
    handler = {"workspace": handlers._run_workspace, "recovery": handlers._run_recovery,
               "components": handlers._run_components}[name]
    ns = SimpleNamespace(command=name, action=command[1], config="task.yaml", target="", kind="",
                         limit=10, backup="", package="", name="ocr", allow_unsigned=False,
                         sha256="")
    monkeypatch.setattr("sys.argv", ["omnicrawl", *command, *args])
    if requires_apply:
        with pytest.raises(ConfirmationRequiredError):
            handler(ns)
        monkeypatch.setattr("sys.argv", ["omnicrawl", *command, *args, "--apply"])
        ns.apply = True
        handler(ns)  # 带 --apply 不抛
    else:
        ns.apply = False
        handler(ns)  # 非破坏性动作直接执行
