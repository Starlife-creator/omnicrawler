from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]


def _load_gate():
    path = _ROOT / "tools" / "check_extras_expansion.py"
    spec = importlib.util.spec_from_file_location("check_extras_expansion", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _copied_root(tmp_path: Path, gate) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        (_ROOT / "pyproject.toml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return tmp_path


def _section_bounds(text: str, name: str) -> tuple[int, int]:
    """Return the [start, end) span of the `name = [ ... ]` block.

    The body contains `]` characters of its own (`paddleocr[doc-parser]`), so the
    closing bracket must be located as a lone `]` on its own line.
    """
    start = text.index(f"{name} = [")
    end = text.index("\n]", start) + 1
    assert text[end] == "]" and text[end + 1] in ("\r", "\n")
    return start, end + 1


def test_real_repository_extras_are_consistent() -> None:
    gate = _load_gate()
    assert gate.check(_ROOT) == []


def test_expansion_keeps_declaration_order_and_drops_duplicates() -> None:
    gate = _load_gate()
    extras = gate.load_extras(_ROOT)
    expansion = gate.expand_feature_extras(extras)
    names = [gate.requirement_name(item) for item in expansion]
    assert len(names) == len(set(names)), "expansion must drop duplicate distributions"
    # 声明序承诺：html 的条目必须排在 document 的条目之前。
    assert names.index("beautifulsoup4") < names.index("python-docx")
    # lxml / openpyxl 各出现在两个功能 extra 里，只保留首次出现的位置。
    assert names.index("lxml") < names.index("playwright")
    assert names.index("openpyxl") < names.index("playwright")


def test_macos_expansion_swaps_the_paddle_packages() -> None:
    gate = _load_gate()
    extras = gate.load_extras(_ROOT)
    macos = gate.expected_macos(gate.expand_feature_extras(extras))
    names = [gate.requirement_name(item) for item in macos]
    assert "paddleocr" not in names
    assert "paddlepaddle" not in names
    assert "opencv-python-headless" in names
    assert names.index("opencv-python-headless") == names.index("numpy") + 1


def test_reverse_assertion_reordered_entry_turns_red(tmp_path: Path) -> None:
    """反向断言：把 full 里两个条目对调 ⇒ 必须指名第几项判红。"""
    gate = _load_gate()
    root = _copied_root(tmp_path, gate)
    pyproject = root / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    swapped = text.replace(
        '"playwright>=1.49,<2", "patchright>=1,<2", "selenium>=4.27,<5"',
        '"patchright>=1,<2", "playwright>=1.49,<2", "selenium>=4.27,<5"',
        1,
    )
    assert swapped != text
    pyproject.write_text(swapped, encoding="utf-8")

    problems = gate.check(root)
    assert len(problems) == 1
    assert "'full'" in problems[0]
    assert "entry #" in problems[0]


def test_reverse_assertion_missing_entry_turns_red(tmp_path: Path) -> None:
    """反向断言：从 full 里删掉一条 ⇒ 红（缺项比错序更严重）。"""
    gate = _load_gate()
    root = _copied_root(tmp_path, gate)
    pyproject = root / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    stripped = text.replace('"duckdb>=1.2,<3", ', "", 1)
    assert stripped != text
    pyproject.write_text(stripped, encoding="utf-8")

    problems = gate.check(root)
    assert problems
    assert any("'full'" in problem for problem in problems)


def test_reverse_assertion_drifted_specifier_turns_red(tmp_path: Path) -> None:
    """反向断言：包名对但版本上界被改写 ⇒ 红（顺序对不算通过）。"""
    gate = _load_gate()
    root = _copied_root(tmp_path, gate)
    pyproject = root / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    start, end = _section_bounds(text, "full")
    body = text[start:end]
    drifted = body.replace('"chardet>=5,<8"', '"chardet>=5,<9"', 1)
    assert drifted != body
    pyproject.write_text(text[:start] + drifted + text[end:], encoding="utf-8")

    problems = gate.check(root)
    assert problems
    assert any("chardet" in problem for problem in problems)


def test_reverse_assertion_empty_expansion_is_not_a_pass(tmp_path: Path) -> None:
    """反向断言：功能 extra 全空 ⇒ 判红而不是恒真。"""
    gate = _load_gate()
    lines = "\n".join(f"{name} = []" for name in gate.FEATURE_EXTRAS)
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "x"\nversion = "0"\n[project.optional-dependencies]\n{lines}\n',
        encoding="utf-8",
    )
    problems = gate.check(tmp_path)
    assert any("empty selection is not a pass" in problem for problem in problems)


def test_reverse_assertion_missing_aggregate_extra_turns_red(tmp_path: Path) -> None:
    """反向断言：full-macos 被删掉 ⇒ 明确报缺，而不是静默跳过。"""
    gate = _load_gate()
    root = _copied_root(tmp_path, gate)
    pyproject = root / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    start, end = _section_bounds(text, "full-macos")
    pyproject.write_text(text[:start] + text[end:], encoding="utf-8")

    problems = gate.check(root)
    assert any("full-macos" in problem for problem in problems)


def test_gate_is_wired_into_the_static_set_and_ci() -> None:
    """登记与接线：新门禁必须同时出现在 gate_registry 与 quality.yml。"""
    registry = (_ROOT / "tools" / "gate_registry.py").read_text(encoding="utf-8")
    assert 'name="check_extras_expansion"' in registry
    workflow = (_ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
    assert "tools/check_extras_expansion.py" in workflow
