"""`tools/check_shell_scripts.py` 静态门禁反向断言（C-#17 / C-#18 / C-#11）。

每一类缺陷都在临时目录装回去，断言脚本判红，再还原确认通过；真实仓库必须全绿。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]

sys.path.insert(0, str(_ROOT))
from tools.gate_registry import SKIP_EXIT_CODE  # noqa: E402


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


# ---------- 环境能力缺失 ⇒ 跳过码，而不是把 9 个没坏的脚本判成语法错误 ----------
#
# 由来（2026-11-13 实测）：Git for Windows 的 bash 在被禁命名管道的沙箱里连启动都做不到
# （`fatal error - couldn't create signal pipe, Win32 error 5`），`bash -n` 对每个脚本
# 都返回非 0，于是 `self_verify --set static` 里这一条恒红、9 个脚本被同时写成
# 「语法错误」——代码没坏，红的是环境。以下用例把这条判据钉住。


def test_bash_capability_probe_detects_usable_bash() -> None:
    """真 bash 可用的环境里，能力探测必须返回 None（否则所有语法检查都会被静默跳过）。"""
    assert GATE.bash_unavailable_reason() is None


def test_unusable_bash_yields_skip_code_not_syntax_noise(monkeypatch, capsys) -> None:
    """bash 不可用 ⇒ 跳过码 + 末行写明能力缺失，且**不得**产出任何「语法错误」。"""
    monkeypatch.setattr(
        GATE, "_bash_executable", lambda: str(_ROOT / "tools" / "definitely-not-a-bash.exe")
    )
    code = GATE.main([])
    captured = capsys.readouterr()
    assert code == SKIP_EXIT_CODE, f"bash 不可用时应返回跳过码，实际 {code}"
    assert "语法错误" not in (captured.err + captured.out), captured
    assert "bash 语法检查无法执行" in captured.err, captured
    # 约定：self_verify 取末行作为跳过原因，末行必须自解释。
    assert captured.err.strip().splitlines()[-1].startswith("bash 语法检查无法执行")


def test_skip_path_does_not_silence_structural_defects(monkeypatch, tmp_path, capsys) -> None:
    """**反向断言**：bash 不可用时结构判据照旧判红——跳过不能变成放水。

    这条防的是「为了消掉假红，把整条门禁变成软通过」：缺 `set -euo pipefail` 的脚本
    在 bash 不可用时仍必须让门禁返回 1，而不是返回跳过码。
    """
    bad = tmp_path / "run_linux.sh"
    bad.write_text(
        "#!/usr/bin/env bash\nset -e\ncd \"$(dirname \"$0\")\"\n", encoding="utf-8"
    )
    monkeypatch.setattr(GATE, "_bash_executable", lambda: "no-such-bash-binary")
    monkeypatch.setattr(GATE, "_shell_scripts", lambda root=None: [bad])

    code = GATE.main([])
    captured = capsys.readouterr()
    assert code == 1, f"存在结构缺陷时必须判红，实际 {code}（跳过码 {SKIP_EXIT_CODE} 不能掩盖它）"
    assert "set -euo pipefail" in captured.err, captured
