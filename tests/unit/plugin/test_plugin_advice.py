from __future__ import annotations

from omnicrawler.plugins.plugin_advice import choose_fetch_advice


def test_fetch_advice_accepts_only_bounded_known_proposals() -> None:
    assert choose_fetch_advice(
        [
            {
                "fetch_advice": {
                    "action": "conditional_revalidate",
                    "reason": "unchanged before" + "x" * 500,
                }
            }
        ]
    ) == {
        "action": "conditional_revalidate",
        "reason": ("unchanged before" + "x" * 500)[:240],
    }
    assert choose_fetch_advice([{"fetch_advice": {"action": "skip"}}]) is None


def test_conflicting_fetch_advice_fails_closed_to_core_policy() -> None:
    assert choose_fetch_advice(
        [
            {"fetch_advice": {"action": "conditional_revalidate"}},
            {"fetch_advice": {"action": "force_fetch"}},
        ]
    ) is None
