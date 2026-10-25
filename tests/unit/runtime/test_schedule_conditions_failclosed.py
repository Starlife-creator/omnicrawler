"""A-P2-5：psutil 缺失时电源 / 网络条件 fail-closed 回归测试。

旧实现把 ``import psutil`` 失败置 None 后直接跳过对应条件（fail-open）；
本文件锁定新语义：条件被配置而依赖不可用时拒绝本次运行并显式标注原因。
"""
from __future__ import annotations

import sys
import types
from datetime import UTC, datetime

import pytest

from omnicrawler.runtime.schedule_conditions import (
    PSUTIL_UNAVAILABLE_REASON,
    evaluate_conditions,
)


def test_no_conditions_passes() -> None:
    allowed, reason = evaluate_conditions({})
    assert allowed is True
    assert reason == ""


def test_hours_condition_independent_of_psutil(monkeypatch: pytest.MonkeyPatch) -> None:
    # 时段条件不依赖 psutil：依赖缺失也不得影响 allowed_hours 判定。
    monkeypatch.setitem(sys.modules, "psutil", None)
    allowed, reason = evaluate_conditions({"allowed_hours": [datetime.now(UTC).hour]})
    assert allowed is True
    assert reason == ""


@pytest.mark.parametrize(
    "condition",
    [{"require_ac": True}, {"minimum_battery_percent": 50}, {"require_network": True}],
)
def test_missing_psutil_fails_closed(monkeypatch: pytest.MonkeyPatch, condition: dict[str, object]) -> None:
    monkeypatch.setitem(sys.modules, "psutil", None)
    allowed, reason = evaluate_conditions(condition)
    assert allowed is False
    assert reason == PSUTIL_UNAVAILABLE_REASON
    assert "psutil" in reason


class _FakeBattery:
    def __init__(self, plugged: bool, percent: float) -> None:
        self.power_plugged = plugged
        self.percent = percent


def _install_fake_psutil(
    monkeypatch: pytest.MonkeyPatch, *, battery: object, network_active: bool
) -> None:
    fake = types.ModuleType("psutil")
    fake.sensors_battery = lambda: battery  # type: ignore[attr-defined]
    fake.net_if_stats = lambda: {  # type: ignore[attr-defined]
        "eth0": types.SimpleNamespace(isup=network_active),
        "lo": types.SimpleNamespace(isup=True),
    }
    monkeypatch.setitem(sys.modules, "psutil", fake)


def test_psutil_present_blocks_on_unplugged(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_psutil(monkeypatch, battery=_FakeBattery(False, 80), network_active=True)
    allowed, reason = evaluate_conditions({"require_ac": True})
    assert allowed is False
    assert "电源" in reason


def test_psutil_present_battery_below_minimum(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_psutil(monkeypatch, battery=_FakeBattery(False, 20), network_active=True)
    allowed, reason = evaluate_conditions({"minimum_battery_percent": 50})
    assert allowed is False
    assert "电量" in reason


def test_psutil_present_network_down_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_psutil(monkeypatch, battery=None, network_active=False)
    allowed, reason = evaluate_conditions({"require_network": True})
    assert allowed is False
    assert "网络" in reason


def test_psutil_present_network_up_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_psutil(monkeypatch, battery=None, network_active=True)
    allowed, _reason = evaluate_conditions({"require_network": True})
    assert allowed is True
