"""Contract tests for `tools/check_silent_exception_handlers.py`.

The gate only has value if it turns red when the discipline is broken, so every
"passes" case here is paired with a reverse assertion that puts the gap back.
"""

from __future__ import annotations

from pathlib import Path

from tools import check_silent_exception_handlers as gate

_SOURCE_ROOT = Path(__file__).resolve().parents[3] / "src" / "omnicrawler"


def _problems_for(source: str) -> list[str]:
    handlers, problems = gate.scan_source(source, path="sample.py")
    assert problems == []
    for handler in handlers:
        names = [name.strip() for name in handler.type_name.split(",")]
        if gate._is_wide(names) and not handler.exempt:
            problems.append(handler.describe())
    return problems


def test_broad_silent_handler_without_marker_is_reported() -> None:
    problems = _problems_for("try:\n    f()\nexcept Exception:\n    pass\n")
    assert len(problems) == 1
    assert "except Exception" in problems[0]
    assert "noqa" in problems[0]


def test_bare_except_silent_handler_is_reported() -> None:
    problems = _problems_for("try:\n    f()\nexcept:\n    pass\n")
    assert len(problems) == 1
    assert "<bare>" in problems[0]


def test_base_exception_silent_handler_is_reported() -> None:
    problems = _problems_for("try:\n    f()\nexcept BaseException:\n    continue\n")
    assert len(problems) == 1


def test_marker_on_the_except_line_excuses_the_handler() -> None:
    source = "try:\n    f()\nexcept Exception:  # noqa: BLE001 - reviewed\n    pass\n"
    assert _problems_for(source) == []


def test_marker_elsewhere_in_the_file_does_not_excuse_the_handler() -> None:
    source = (
        "# noqa: BLE001  (a comment far away must not excuse anything)\n"
        "try:\n    f()\nexcept Exception:\n    pass\n"
    )
    assert len(_problems_for(source)) == 1


def test_narrow_handler_may_swallow_silently() -> None:
    source = "try:\n    import optional_lib\nexcept ImportError:\n    pass\n"
    assert _problems_for(source) == []


def test_handler_with_a_body_other_than_pass_or_continue_is_not_silent() -> None:
    source = "try:\n    f()\nexcept Exception:\n    g()\n"
    assert _problems_for(source) == []


def test_handler_with_several_statements_is_not_silent() -> None:
    source = "try:\n    f()\nexcept Exception:\n    g()\n    pass\n"
    assert _problems_for(source) == []


def test_tuple_handler_is_reported_when_one_member_is_broad() -> None:
    source = "try:\n    f()\nexcept (ValueError, Exception):\n    pass\n"
    assert len(_problems_for(source)) == 1


def test_scanning_zero_handlers_is_not_a_pass() -> None:
    empty = _SOURCE_ROOT / "_does_not_exist_for_this_test"
    problems = gate.check(empty)
    assert len(problems) == 1
    assert "empty selection is not a pass" in problems[0]


def test_reverse_assertion_real_tree_turns_red_when_the_gap_is_put_back() -> None:
    """Put the original gap back into a real file and the gate must name it.

    This is the byte-level reverse assertion: the fixed site is restored to the exact
    pre-fix shape (`except Exception: continue`, no log, no marker), so a gate that
    stayed green would be proving nothing.
    """
    source = (_SOURCE_ROOT / "visual_selector" / "similarity.py").read_text(encoding="utf-8")
    assert "# noqa: BLE001" in source
    fixed = (
        "        except Exception as exc:  # noqa: BLE001 - 探测失败只跳过该层候选，但必须留痕\n"
        '            LOGGER.debug("xpath 探测失败，跳过该层候选 %s: %s", test_xpath, exc)\n'
        "            continue\n"
    )
    broken = "        except Exception:\n            continue\n"
    assert fixed in source, "the fixed handler shape changed; update this reverse assertion"
    stripped = source.replace(fixed, broken)
    assert stripped != source
    handlers, problems = gate.scan_source(stripped, path="src/omnicrawler/visual_selector/similarity.py")
    assert problems == []
    offenders = [
        handler
        for handler in handlers
        if gate._is_wide([name.strip() for name in handler.type_name.split(",")]) and not handler.exempt
    ]
    assert len(offenders) == 1
    assert "similarity.py" in offenders[0].describe()


def test_real_source_tree_is_clean() -> None:
    assert gate.check(_SOURCE_ROOT) == []


def test_main_reports_success_in_ascii() -> None:
    assert gate.main(["--root", str(_SOURCE_ROOT)]) == 0
