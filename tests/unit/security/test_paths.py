"""P9-A3：工作区包含性 + run_id 集中校验测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawl.security.paths import require_workspace_path
from omnicrawl.state import StateStore


class TestRequireWorkspacePath:
    def test_absolute_path_inside_passes(self, tmp_path: Path) -> None:
        target = tmp_path / "sub" / "a.txt"
        target.parent.mkdir()
        assert require_workspace_path(target, root=tmp_path) == target.resolve()

    def test_relative_path_resolves_against_root(self, tmp_path: Path) -> None:
        target = tmp_path / "sub" / "a.txt"
        target.parent.mkdir()
        # 相对路径按 root 解析（而非进程 CWD）
        assert require_workspace_path("sub/a.txt", root=tmp_path) == target.resolve()

    def test_relative_traversal_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="越出工作区"):
            require_workspace_path("../outside.txt", root=tmp_path)

    def test_absolute_outside_rejected(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "outside.txt"
        outside.write_text("x")
        with pytest.raises(ValueError, match="越出工作区"):
            require_workspace_path(outside, root=tmp_path)

    def test_what_label_in_error(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="场景导入 YAML"):
            require_workspace_path("../scene.yaml", root=tmp_path, what="场景导入 YAML")


class TestStateStoreRunIdValidation:
    def _store(self, tmp_path: Path) -> StateStore:
        return StateStore(tmp_path / "state.sqlite3")

    def test_legit_run_id_accepted(self, tmp_path: Path) -> None:
        with self._store(tmp_path) as state:
            rid = state.start_run("p", "c")
            assert state._require_run_id(rid) == rid
            assert state._require_run_id("a-b_c1") == "a-b_c1"

    def test_none_passes_for_optional_filter(self, tmp_path: Path) -> None:
        with self._store(tmp_path) as state:
            assert state._require_run_id(None) is None

    def test_injection_forms_rejected(self, tmp_path: Path) -> None:
        with self._store(tmp_path) as state:
            for bad in ("x' OR 1=1 --", "../etc", "a/b", "a b", "x" * 81, ""):
                with pytest.raises(ValueError, match="run_id 含非法字符"):
                    state._require_run_id(bad)

    def test_finish_run_rejects_invalid(self, tmp_path: Path) -> None:
        with self._store(tmp_path) as state:
            with pytest.raises(ValueError, match="run_id 含非法字符"):
                state.finish_run("x' OR 1=1 --", "completed", {})
