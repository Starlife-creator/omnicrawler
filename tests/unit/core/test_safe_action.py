from __future__ import annotations

import pytest

from omnicrawl.core.safe_action import (
    ConfirmationRequiredError,
    SafeActionContext,
    default_trash_root,
    is_workspace_path,
    move_to_recycle,
    require_explicit_apply,
    require_known_stage,
)


class TestRequireExplicitApply:
    def test_apply_flag_passes(self) -> None:
        require_explicit_apply("reset", ["reset", "--apply"])

    def test_yes_flag_passes(self) -> None:
        require_explicit_apply("reset", ["reset", "--yes"])

    def test_missing_flag_raises(self) -> None:
        with pytest.raises(ConfirmationRequiredError, match="破坏性操作"):
            require_explicit_apply("reset", ["reset"])


class TestRequireKnownStage:
    def test_known_stage_passes(self) -> None:
        require_known_stage("parse", {"parse", "ocr"})

    def test_unknown_stage_raises(self) -> None:
        with pytest.raises(ValueError, match="未知阶段"):
            require_known_stage("nope", {"parse", "ocr"})


class TestIsWorkspacePath:
    def test_work_dir_is_workspace(self) -> None:
        assert is_workspace_path(__import__("pathlib").Path("work/project"))

    def test_home_file_is_not_workspace(self) -> None:
        assert not is_workspace_path(__import__("pathlib").Path("C:/Windows/system32/evil.exe"))


class TestMoveToRecycle:
    def test_missing_target_is_noop(self, tmp_path) -> None:
        result = move_to_recycle(tmp_path / "ghost", trash_root=tmp_path / "trash")
        assert result == (tmp_path / "ghost").resolve()

    def test_rejects_non_workspace(self, tmp_path) -> None:
        victim = tmp_path / "outside.txt"
        victim.write_text("x", encoding="utf-8")
        with pytest.raises(ValueError, match="非工作区"):
            move_to_recycle(victim, trash_root=tmp_path / "trash")

    def test_moves_to_trash(self, tmp_path) -> None:
        work = tmp_path / "work"
        work.mkdir()
        victim = work / "data.db"
        victim.write_text("x", encoding="utf-8")
        moved = move_to_recycle(victim, trash_root=tmp_path / "trash")
        assert not victim.exists()
        assert moved.exists()
        assert moved.read_text(encoding="utf-8") == "x"

    def test_conflict_gets_timestamp_suffix(self, tmp_path) -> None:
        work = tmp_path / "work"
        work.mkdir()
        first = work / "log.json"
        second = work / "log.json"
        first.write_text("1", encoding="utf-8")
        trash = tmp_path / "trash"
        move_to_recycle(first, trash_root=trash)
        second.write_text("2", encoding="utf-8")
        moved = move_to_recycle(second, trash_root=trash)
        assert moved != trash / "log.json"
        assert moved.exists()


class TestContext:
    def test_plans_are_collected(self) -> None:
        ctx = SafeActionContext()
        ctx.plan("reset", __import__("pathlib").Path("work/x"))
        assert len(ctx.planned) == 1
        assert ctx.planned[0].label == "reset"


def test_default_trash_root_is_under_data_root() -> None:
    assert default_trash_root().name == "recycle"
