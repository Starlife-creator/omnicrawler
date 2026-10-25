"""`tools/check_shell_scripts.py` 静态门禁反向断言（C-#17 / C-#18 / C-#11）。

每一类缺陷都在临时目录装回去，断言脚本判红，再还原确认通过；真实仓库必须全绿。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]


def _load():
    spec = importlib.util.spec_from_file_location(
        "_check_shell_scripts_under_test", _ROOT / "tools" / "check_shell_scripts.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


GATE = _load()


def test_real_repo_shell_scripts_pass() -> None:
    issues = GATE.check(_ROOT)
    assert issues == [], issues


def test_bash_script_without_pipefail_is_red(tmp_path: Path) -> None:
    (tmp_path / "run_gui_linux.sh").write_text(
        "#!/usr/bin/env bash\nset -e\ncd \"$(dirname \"$0\")\"\n"
        'if [ ! -x .venv/bin/python ]; then echo "x" >&2; exit 1; fi\n'
        "exec .venv/bin/python -m omnicrawler.gui \"$@\"\n",
        encoding="utf-8",
    )
    issues = GATE.check(tmp_path)
    assert any("set -euo pipefail" in issue for issue in issues), issues


def test_launcher_without_venv_guard_is_red(tmp_path: Path) -> None:
    (tmp_path / "run_workbench_linux.sh").write_text(
        "#!/bin/sh\nset -eu\ncd \"$(dirname \"$0\")\"\n"
        "exec .venv/bin/python -m omnicrawler workbench \"$@\"\n",
        encoding="utf-8",
    )
    issues = GATE.check(tmp_path)
    assert any(".venv/bin/python 存在性守卫" in issue for issue in issues), issues
    assert any("stderr" in issue for issue in issues), issues


def test_setup_script_without_python_gate_is_red(tmp_path: Path) -> None:
    (tmp_path / "setup_linux.sh").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\ncd \"$(dirname \"$0\")\"\n"
        "python3 -m venv .venv\n",
        encoding="utf-8",
    )
    issues = GATE.check(tmp_path)
    assert any("3.12" in issue for issue in issues), issues


def test_setup_script_referencing_stale_tool_is_red(tmp_path: Path) -> None:
    (tmp_path / "setup_macos.command").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\ncd \"$(dirname \"$0\")\"\n"
        "if ! \"$PYTHON_BIN\" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'; then exit 1; fi\n"
        "python tools/download_ocr_models.py .runtime/models/paddlex\n",
        encoding="utf-8",
    )
    issues = GATE.check(tmp_path)
    assert any("download_ocr_models.py" in issue for issue in issues), issues


def test_build_script_installing_dev_extra_is_red(tmp_path: Path) -> None:
    (tmp_path / "build_linux.sh").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\ncd \"$(dirname \"$0\")\"\n"
        "uv sync --locked --no-dev --extra dev\n",
        encoding="utf-8",
    )
    issues = GATE.check(tmp_path)
    assert any("dev" in issue and "extra" in issue for issue in issues), issues
    assert any("build" in issue and "extra" in issue for issue in issues), issues


def test_syntax_broken_script_is_red(tmp_path: Path) -> None:
    (tmp_path / "run_linux.sh").write_text(
        "#!/bin/sh\nset -eu\nif [ -z \"X\"\n", encoding="utf-8"
    )
    issues = GATE.check(tmp_path)
    assert any("bash -n 语法错误" in issue for issue in issues), issues


def test_clean_synthetic_scripts_pass(tmp_path: Path) -> None:
    (tmp_path / "run_linux.sh").write_text(
        "#!/bin/sh\nset -eu\ncd \"$(dirname \"$0\")\"\n"
        'if [ ! -x .venv/bin/python ]; then echo "[ERROR] missing venv" >&2; exit 1; fi\n'
        "exec .venv/bin/python -m omnicrawler \"$@\"\n",
        encoding="utf-8",
    )
    assert GATE.check(tmp_path) == []
