"""`theme.apply` 能力：运行期切换**已注册**主题（2026-11-27）。

## 为什么要单独一条能力

`ui:theme` 是**加载期**声明（`register(registry)` 里注册 `ThemeRegistration`），
并且对**本地**插件**自动批准**（`plugin_loader` 里 `approved |= UI_PERMISSIONS`）。
如果运行期也走它，等于「任何本地插件无需审批即可随时改整个界面配色」——
那是权限提升，不是功能。

所以运行期走独立的 `theme:apply`：它**不在** `UI_PERMISSIONS` 里。
"""

from __future__ import annotations

import pytest

from omnicrawler.plugins.plugin_broker import CapabilityBroker
from omnicrawler.plugins.plugin_broker_contracts import (
    _CAPABILITY_PERMISSIONS,
    CAPABILITY_VERSIONS,
    E_CONTRACT,
    E_INTERNAL,
    E_PERMISSION,
    E_RESOURCE,
    CapabilityError,
)
from omnicrawler.plugins.plugin_broker_host import (
    register_theme_apply_handler,
    theme_apply_handler,
)
from omnicrawler.plugins.plugin_contracts import UI_PERMISSIONS
from omnicrawler.plugins.plugin_sandbox import ALLOWED_PERMISSIONS


@pytest.fixture(autouse=True)
def _clean_handler():
    """每个用例后清掉注册的回调（避免用例间串味）。"""
    yield
    register_theme_apply_handler(None)


def _broker(permissions: set[str] | None = None) -> CapabilityBroker:
    return CapabilityBroker(
        permissions=permissions if permissions is not None else {"theme:apply"},
        system_info={},
    )


# ── 权限模型 ────────────────────────────────────────────────────────────


def test_capability_is_registered_with_a_version() -> None:
    assert CAPABILITY_VERSIONS["theme.apply"] >= 1
    assert _CAPABILITY_PERMISSIONS["theme.apply"] == "theme:apply"


def test_theme_apply_is_not_in_the_auto_approved_ui_family() -> None:
    """★ 核心判据：`theme:apply` **不在** `UI_PERMISSIONS` 里。

    在的话，任何本地插件无需审批就能随时改整个界面配色 —— 权限提升。
    """
    assert "theme:apply" not in UI_PERMISSIONS
    assert "ui:theme" in UI_PERMISSIONS, "（前提：ui:theme 确实是自动批准族）"


def test_theme_apply_is_an_allowed_permission() -> None:
    """它必须是合法权限名，否则插件清单写了会被判未知权限。"""
    assert "theme:apply" in ALLOWED_PERMISSIONS


def test_missing_permission_is_denied() -> None:
    register_theme_apply_handler(lambda _tid: True)
    with pytest.raises(CapabilityError) as excinfo:
        _broker(set()).dispatch("theme.apply", {"theme_id": "ocean"})
    assert excinfo.value.code == E_PERMISSION


# ── 宿主未注册回调 ──────────────────────────────────────────────────────


def test_unbound_host_reports_internal_error_not_silent_success() -> None:
    """★ 宿主没接回调时必须**明确报错**，不能假装成功。"""
    with pytest.raises(CapabilityError) as excinfo:
        _broker().dispatch("theme.apply", {"theme_id": "ocean"})
    assert excinfo.value.code == E_INTERNAL


# ── 正常路径 ────────────────────────────────────────────────────────────


def test_applies_a_registered_theme() -> None:
    seen: list[str] = []
    register_theme_apply_handler(lambda tid: (seen.append(tid), True)[1])
    result = _broker().dispatch("theme.apply", {"theme_id": "Ocean"})
    assert result == {"applied": True, "theme_id": "ocean"}
    assert seen == ["ocean"], "theme_id 必须先 normalize 再交给宿主"


def test_unregistered_theme_is_an_error_not_a_silent_noop() -> None:
    """★ 未注册的 theme_id ⇒ E_RESOURCE。

    静默忽略会让插件作者以为「注册成功但不生效」，无从调试。
    """
    register_theme_apply_handler(lambda tid: tid == "ocean")
    with pytest.raises(CapabilityError) as excinfo:
        _broker().dispatch("theme.apply", {"theme_id": "not-there"})
    assert excinfo.value.code == E_RESOURCE
    assert "未在宿主注册" in str(excinfo.value)


def test_empty_theme_id_is_a_contract_error() -> None:
    register_theme_apply_handler(lambda _tid: True)
    with pytest.raises(CapabilityError) as excinfo:
        _broker().dispatch("theme.apply", {})
    assert excinfo.value.code == E_CONTRACT


def test_host_value_error_maps_to_contract_error() -> None:
    """宿主侧的 `ValueError`（例如非法 id）要映射成 E_CONTRACT，不是 500。"""

    def _boom(_tid: str) -> bool:
        raise ValueError("非法主题 ID")

    register_theme_apply_handler(_boom)
    with pytest.raises(CapabilityError) as excinfo:
        _broker().dispatch("theme.apply", {"theme_id": "ocean"})
    assert excinfo.value.code == E_CONTRACT


def test_handler_can_be_unregistered() -> None:
    register_theme_apply_handler(lambda _tid: True)
    assert theme_apply_handler() is not None
    register_theme_apply_handler(None)
    assert theme_apply_handler() is None
