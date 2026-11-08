from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]


def _load_gate():
    path = _ROOT / "tools" / "check_build_profiles.py"
    spec = importlib.util.spec_from_file_location("check_build_profiles", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_real_repository_profiles_are_consistent() -> None:
    gate = _load_gate()
    assert gate.check(_ROOT) == []


def test_every_profile_has_a_known_script_site() -> None:
    gate = _load_gate()
    profiles = gate.load_profiles()
    assert profiles, "no profiles declared"
    assert set(profiles) == set(gate.PROFILE_SITES)


def test_declared_extras_exist_in_pyproject() -> None:
    gate = _load_gate()
    known = gate.declared_extras(_ROOT)
    for name, extras in gate.load_profiles().items():
        assert gate._profile_extras_are_known(extras, known) == [], f"{name} references unknown extras"


def test_reverse_assertion_drifted_literal_turns_red(tmp_path: Path) -> None:
    """反向断言：把某个脚本里的 extras 改一个字符 ⇒ 必须指名该 profile 判红。"""
    gate = _load_gate()
    root = tmp_path
    (root / "constraints").mkdir()
    (root / "constraints" / "profiles.toml").write_text(
        (gate.PROFILES_PATH).read_text(encoding="utf-8"), encoding="utf-8"
    )
    for rel in {rel for rel, _ in gate.PROFILE_SITES.values()}:
        text = (_ROOT / rel).read_text(encoding="utf-8", errors="replace")
        (root / rel).write_text(text, encoding="utf-8")
    (root / "pyproject.toml").write_text(
        (_ROOT / "pyproject.toml").read_text(encoding="utf-8"), encoding="utf-8"
    )

    dockerfile = root / "Dockerfile"
    dockerfile.write_text(
        dockerfile.read_text(encoding="utf-8").replace(
            'ARG EXTRAS="html,async-http,streams"', 'ARG EXTRAS="html,async-http"'
        ),
        encoding="utf-8",
    )
    problems = gate.check(root)
    assert len(problems) == 1
    assert "'docker'" in problems[0]
    assert "html,async-http" in problems[0]


def test_reverse_assertion_missing_script_turns_red(tmp_path: Path) -> None:
    """反向断言：声明了 profile 但脚本被删 ⇒ 红，不能静默跳过。"""
    gate = _load_gate()
    (tmp_path / "constraints").mkdir()
    (tmp_path / "constraints" / "profiles.toml").write_text(
        gate.PROFILES_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "pyproject.toml").write_text(
        (_ROOT / "pyproject.toml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    problems = gate.check(tmp_path)
    assert any("is missing" in problem for problem in problems)
    assert any("'docker'" in problem for problem in problems)


def test_reverse_assertion_empty_declaration_is_not_a_pass(tmp_path: Path) -> None:
    """反向断言：一个 profile 都没声明 ⇒ 判红而不是恒真。"""
    gate = _load_gate()
    (tmp_path / "constraints").mkdir()
    (tmp_path / "constraints" / "profiles.toml").write_text("[profiles]\n", encoding="utf-8")
    problems = gate.check(tmp_path)
    assert any("empty selection is not a pass" in problem for problem in problems)


def test_reverse_assertion_unknown_extra_turns_red(tmp_path: Path) -> None:
    """反向断言：profile 里写了一个 pyproject 不存在的 extra ⇒ 红。"""
    gate = _load_gate()
    (tmp_path / "constraints").mkdir()
    (tmp_path / "constraints" / "profiles.toml").write_text(
        '[profiles.docker]\nextras = "html,ghost-extra"\n', encoding="utf-8"
    )
    (tmp_path / "pyproject.toml").write_text(
        (_ROOT / "pyproject.toml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    problems = gate.check(tmp_path)
    assert any("ghost-extra" in problem for problem in problems)


def test_reverse_assertion_undeclared_site_is_reported(tmp_path: Path) -> None:
    """反向断言：脚本里有档位但 profiles.toml 没声明 ⇒ 报「未声明」。"""
    gate = _load_gate()
    problems = gate._site_mismatches(
        {"docker": "html,async-http,streams"}, gate.PROFILE_SITES, _ROOT
    )
    assert any("not declared in profiles.toml" in problem for problem in problems)
