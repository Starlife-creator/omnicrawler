from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]


def _load_checker():
    path = _ROOT / "tools" / "check_extra_install.py"
    spec = importlib.util.spec_from_file_location("check_extra_install", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _declared_extras() -> dict[str, list[str]]:
    return tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "optional-dependencies"
    ]


def _declared_feature_extras() -> set[str]:
    """pyproject 里所有「装了会多一组能力」的 extra（排除聚合型与 dev/build）。"""
    checker = _load_checker()
    return set(_declared_extras()) - set(checker.AGGREGATE_EXTRAS)


def _profile_contract_mismatches(declared: set[str], registered: set[str]) -> list[str]:
    """返回 pyproject 与导入契约的差集描述；空列表表示两者一致。"""
    problems: list[str] = []
    missing = sorted(declared - registered)
    extra = sorted(registered - declared)
    if missing:
        problems.append(f"extra(s) declared in pyproject but with no import contract: {missing}")
    if extra:
        problems.append(f"import contract registered for non-existent extra(s): {extra}")
    if not declared:
        problems.append("empty selection is not a pass: no feature extras parsed from pyproject.toml")
    return problems


def test_every_declared_feature_extra_has_an_import_contract() -> None:
    """新增一个 extra 却不登记导入契约 ⇒ 红（否则就是「声明了但没人真的装过」）。"""
    checker = _load_checker()
    assert _profile_contract_mismatches(
        _declared_feature_extras(), set(checker.PROFILE_IMPORTS)
    ) == []


def test_unknown_profile_is_rejected() -> None:
    checker = _load_checker()
    assert checker.check("not-a-profile") == ["unknown feature profile: not-a-profile"]


def test_heavy_profiles_are_excluded_from_ci_with_a_reason() -> None:
    """不进 CI matrix 的 profile 必须写明理由，且必须仍是合法 profile。"""
    checker = _load_checker()
    for name, reason in checker.HEAVY_PROFILES.items():
        assert name in checker.PROFILE_IMPORTS, f"HEAVY_PROFILES 里的 {name} 不是 feature extra"
        assert reason.strip(), f"{name} 没有写明不进 CI 的理由"
        assert name not in checker.ci_matrix_profiles()


def test_ci_matrix_profiles_cover_every_non_heavy_extra() -> None:
    checker = _load_checker()
    expected = set(checker.PROFILE_IMPORTS) - set(checker.HEAVY_PROFILES)
    assert expected, "ci_matrix_profiles() must not be empty"
    assert set(checker.ci_matrix_profiles()) == expected


def test_quality_workflow_installs_each_feature_in_isolation() -> None:
    checker = _load_checker()
    workflow = (_ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
    job = workflow[workflow.index("  feature-install:"):workflow.index("  test:")]
    expected = ", ".join(checker.ci_matrix_profiles())
    assert f"profile: [{expected}]" in job, (
        "quality.yml 的 feature-install matrix 必须等于 "
        f"check_extra_install.ci_matrix_profiles()：期望 [{expected}]"
    )
    assert 'pip install -e ".[${{ matrix.profile }}]"' in job
    assert 'check_extra_install.py "${{ matrix.profile }}"' in job


def test_ai_transport_and_document_features_are_declared_and_installed_in_ci():
    extras = _declared_extras()
    assert any(spec.startswith("aiohttp") for spec in extras.get("ai", []))
    for edition in ("full", "full-macos"):
        assert any(spec.startswith("aiohttp") for spec in extras[edition])
    workflow = (_ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
    core = workflow[workflow.index("  test:"):workflow.index("  docker:")]
    assert "[html,pdf,dev,async-http,gui,tls,storage,ai,document]" in core


def test_reverse_assertion_unregistered_extra_turns_red() -> None:
    """反向断言：把某个已登记的 extra 从「契约」里抽掉 ⇒ 必须报 missing，不能静默通过。"""
    checker = _load_checker()
    declared = _declared_feature_extras()
    registered = set(checker.PROFILE_IMPORTS) - {"search"}
    problems = _profile_contract_mismatches(declared, registered)
    assert len(problems) == 1
    assert "['search']" in problems[0]


def test_reverse_assertion_stale_contract_entry_turns_red() -> None:
    """反向断言：契约里登记了 pyproject 不存在的 extra ⇒ 必须报多余登记。"""
    declared = _declared_feature_extras()
    checker = _load_checker()
    registered = set(checker.PROFILE_IMPORTS) | {"ghost-extra"}
    problems = _profile_contract_mismatches(declared, registered)
    assert len(problems) == 1
    assert "['ghost-extra']" in problems[0]


def test_reverse_assertion_empty_selection_is_not_a_pass() -> None:
    """反向断言：一个 extra 都没解析出来时，一致性检查必须判红而不是恒真。"""
    checker = _load_checker()
    problems = _profile_contract_mismatches(set(), set(checker.PROFILE_IMPORTS))
    assert any("empty selection is not a pass" in problem for problem in problems)


def test_independent_ai_extra_reports_missing_transport(monkeypatch):
    checker = _load_checker()
    original = checker.importlib.import_module

    def unavailable(name):
        if name == "aiohttp":
            raise ModuleNotFoundError("transport absent")
        return original(name)

    monkeypatch.setattr(checker.importlib, "import_module", unavailable)
    assert checker.check("ai") == ["ai cannot import aiohttp: ModuleNotFoundError: transport absent"]
