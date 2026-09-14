"""`install_windows.ps1` 的 Python 版本闸门 —— 结构断言，不是全文比对。

## 起因（audit-20260805 `report_packaging_scripts.md` 的 high 条）

脚本用 `py -3` 建 venv，**没有版本上界**：机器上最新的是 3.10/3.11 时，venv 照样建得起来，
直到 `pip install -e '.[full,dev]'` 才失败（报错点是 pip 的 requires-python），
排查成本很高；而且当时的提示语写着 "Python 3.10 or newer"，与 `requires-python = ">=3.12"` 不符。

## 为什么写成结构断言

脚本措辞会变（中文注释、提示语），但两件事不变：
① **装依赖之前必须校验解释器版本**；② 校验语句必须在 `$Python` 被赋值**之后**
（我第一次就插错了位置 —— 把闸门放在 `$Python = Join-Path …` 之前，运行时拿到的会是空变量，
静默失效。这条因此专门钉住）。

最低版本**从 `pyproject.toml` 的 `requires-python` 读**，不在这里硬编码，避免两处漂移。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "install_windows.ps1"
PYPROJECT = REPO_ROOT / "pyproject.toml"


def _script() -> str:
    assert SCRIPT.is_file(), f"缺少 {SCRIPT.name}"
    return SCRIPT.read_text(encoding="utf-8-sig")


def _required_python() -> str:
    """从 pyproject 取最低 Python 版本（形如 "3.12"）。"""
    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'requires-python\s*=\s*">=\s*([0-9]+\.[0-9]+)', text)
    assert match, "pyproject.toml 里没有 requires-python >=X.Y"
    return match.group(1)


def test_version_gate_exists_before_installing_dependencies() -> None:
    text = _script()
    gate_index = text.find("version_info")
    assert gate_index != -1, "install_windows.ps1 缺少解释器版本校验（py -3 可能选到过旧版本）"

    install_index = text.find("pip install -e")
    assert install_index != -1, "未找到依赖安装语句，脚本结构可能已变"
    assert gate_index < install_index, "版本校验必须发生在装依赖之前，否则报错点会离原因很远"


def test_version_gate_runs_after_the_interpreter_variable_is_assigned() -> None:
    """闸门必须用**已赋值**的 `$Python`；放在赋值之前会拿到空变量（静默失效）。"""
    text = _script()
    assign_index = text.find("$Python = Join-Path")
    assert assign_index != -1, "未找到 $Python 赋值语句"
    gate_index = text.find("version_info")
    assert assign_index < gate_index, (
        "版本闸门出现在 `$Python = Join-Path …` 之前 —— 此时变量还是空的，校验不会生效"
    )


def test_gate_uses_the_version_from_pyproject_not_a_hardcoded_one() -> None:
    text = _script()
    required = _required_python()
    major, minor = required.split(".")
    pattern = rf"version_info\s*>=\s*\(\s*{major}\s*,\s*{minor}\b"
    assert re.search(pattern, text), (
        f"版本闸门未按 pyproject 的 requires-python（>={required}）判断；"
        "请从 pyproject 读，避免脚本与打包元数据各说一套"
    )


def test_stale_python_version_hint_is_gone() -> None:
    """过时提示语会被用户当成真实要求 —— 与 requires-python 不一致时必须改掉。"""
    text = _script()
    required = _required_python()
    stale = re.findall(r"Python\s+3\.(\d+)\s+or newer", text)
    assert stale, "未找到「Python X.Y or newer」提示语（脚本可能已改写，请同步本用例）"
    for minor in stale:
        assert f"3.{minor}" == required, (
            f"提示语写的是 Python 3.{minor} or newer，而 requires-python 要求 >={required}"
        )
