"""`quality/diagnostic_experience.py` 的守卫：诊断映射与**支持包脱敏**。

## 为什么这个模块值得测（此前 0% 覆盖）

它有两件与安全/可用性直接相关的事：

1. **诊断映射**：把内部诊断报告的类别映射成面向用户的 `category`。
   用户/agent 靠这个字段决定下一步（`rate_limit` 该等待、`disk` 该清理）。
   映射错了会把人引向错误的排查方向 —— 与我今天修的那个
   `PermissionError → 检查网络代理` 是同一类问题。

2. **支持包脱敏**：`create_redacted_support_bundle()` 生成用户要发给客服的
   ZIP。**它必须真的脱敏** —— 这个包里会带诊断信息与日志，如果凭据泄漏
   进去，用户是在毫无察觉的情况下把密码发出去。

## 判据

* 映射表覆盖所有已知类别，未知类别原样透传（不吞）；
* 支持包**必须**含三件套（diagnostic.json / logs-redacted.txt / PRIVACY.txt）；
* **凭据不得出现在包内任何文件里**（这条是核心，用真实 token 形态验证）；
* 目标路径受 `require_workspace_path` 约束（不能写到工作区外）。

## 边界

不验诊断引擎本身的判据（`quality/diagnostics.py` 有独立测试）。
这里只管「映射与出包」这一层。
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from omnicrawler.quality.diagnostic_experience import (
    UserFacingDiagnostic,
    create_redacted_support_bundle,
    diagnose,
)

#: 内部类目 → 面向用户的类目（与实现里的映射表一致）。
_EXPECTED_MAPPING = {
    "rate_limit": "rate_limit",
    "login": "login",
    "disk": "disk",
    "component": "component",
    "page_change": "page_change",
}


def _diagnostic(**overrides) -> UserFacingDiagnostic:
    base = dict(
        category="disk",
        what_happened="磁盘空间不足",
        possible_causes=("缓存未清理",),
        attempts=("重试 1 次",),
        actions=("清理缓存", "查看失败样本"),
        data_impact="本次结果未保存",
        recoverable=True,
        help_id="HELP-DISK-01",
    )
    base.update(overrides)
    return UserFacingDiagnostic(**base)


def test_diagnose_returns_user_facing_shape() -> None:
    """`diagnose()` 必须返回齐全的面向用户字段（UI 直接展示）。"""
    result = diagnose("429 Too Many Requests")
    assert isinstance(result, UserFacingDiagnostic)
    assert result.category, "category 为空 —— 调用方无法分支"
    assert result.what_happened, "没有说明发生了什么"
    assert result.actions, "没有给出可执行动作"
    assert isinstance(result.recoverable, bool)
    assert result.help_id, "没有 help_id（帮助锚点）"


def test_diagnose_keeps_attempts_as_given() -> None:
    """调用方传入的 `attempts` 必须原样带上（用于「我试过这些」的上下文）。"""
    attempts = ("已重试 2 次", "已更换代理")
    result = diagnose("connection reset", attempts=attempts)
    assert result.attempts == attempts


def test_diagnose_actions_always_contain_view_samples() -> None:
    """动作里必须包含「查看失败样本」—— 那是排查的核心入口。"""
    result = diagnose("unknown failure")
    assert any("样本" in action for action in result.actions), (
        f"动作里没有「查看失败样本」：{result.actions}"
    )


def test_category_is_never_empty_for_varied_inputs() -> None:
    """多种输入下 category 都不能为空（否则调用方分支失效）。"""
    for message in (
        "429 rate limited",
        "403 forbidden login required",
        "No space left on device",
        "ImportError: No module named 'x'",
        "selector not found",
        "完全未知的一种错误",
    ):
        result = diagnose(message)
        assert result.category, f"输入 {message!r} 的 category 为空"


def test_diagnostic_is_frozen() -> None:
    """DTO 是不可变的（`frozen=True`）—— 防止下游悄悄改诊断内容。"""
    from dataclasses import FrozenInstanceError

    result = diagnose("disk full")
    with pytest.raises(FrozenInstanceError):
        result.category = "changed"  # type: ignore[misc]


def test_support_bundle_contains_three_files(tmp_path: Path) -> None:
    """支持包必须含三件套（缺任何一个，客服/用户都看不懂）。"""
    target = tmp_path / "support.zip"
    create_redacted_support_bundle(target, _diagnostic(), ("line1",), root=tmp_path)
    assert target.is_file(), "支持包没生成"
    with zipfile.ZipFile(target) as archive:
        names = set(archive.namelist())
    assert {"diagnostic.json", "logs-redacted.txt", "PRIVACY.txt"} <= names, (
        f"支持包缺文件：{names}"
    )


def test_support_bundle_round_trips_diagnostic(tmp_path: Path) -> None:
    """`diagnostic.json` 必须能解析回同一份诊断内容。"""
    target = tmp_path / "support.zip"
    original = _diagnostic(category="rate_limit")
    create_redacted_support_bundle(target, original, (), root=tmp_path)
    with zipfile.ZipFile(target) as archive:
        payload = json.loads(archive.read("diagnostic.json").decode("utf-8"))
    assert payload["category"] == "rate_limit"
    assert payload["what_happened"] == original.what_happened
    assert payload["recoverable"] is True


def test_support_bundle_redacts_credentials_from_logs(tmp_path: Path) -> None:
    """**核心安全守卫**：日志里的凭据不得出现在包内。

    支持包是用户主动发给别人的东西。若 token/password 原样带出去，
    用户是在毫无察觉的情况下泄漏了凭据。
    """
    target = tmp_path / "support.zip"
    secrets = (
        "Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz",
        "password=SuperSecret123",
        "Cookie: sessionid=abcdef123456",
        "api_key: AKIAIOSFODNN7EXAMPLE",
    )
    create_redacted_support_bundle(target, _diagnostic(), secrets, root=tmp_path)
    with zipfile.ZipFile(target) as archive:
        blob = b"".join(archive.read(name) for name in archive.namelist()).decode(
            "utf-8", errors="replace"
        )
    for leaked in ("sk-abcdefghijklmnopqrstuvwxyz", "SuperSecret123", "abcdef123456", "AKIAIOSFODNN7EXAMPLE"):
        assert leaked not in blob, (
            f"凭据 {leaked!r} 原样出现在支持包里 —— 脱敏失效"
        )


def test_support_bundle_refuses_path_outside_root(tmp_path: Path) -> None:
    """目标路径必须落在 root 内（`require_workspace_path` 的约束）。"""
    outside = tmp_path.parent / "escaped-support.zip"
    with pytest.raises(ValueError):
        create_redacted_support_bundle(outside, _diagnostic(), (), root=tmp_path)


def test_support_bundle_creates_parent_dirs(tmp_path: Path) -> None:
    """父目录不存在时要自动创建（用户不会预先建好目录层级）。"""
    target = tmp_path / "nested" / "deep" / "support.zip"
    create_redacted_support_bundle(target, _diagnostic(), (), root=tmp_path)
    assert target.is_file()


def test_privacy_notice_is_present_and_actionable(tmp_path: Path) -> None:
    """PRIVACY.txt 必须提示用户「发送前复核」，而不是只说「已脱敏」。"""
    target = tmp_path / "support.zip"
    create_redacted_support_bundle(target, _diagnostic(), (), root=tmp_path)
    with zipfile.ZipFile(target) as archive:
        notice = archive.read("PRIVACY.txt").decode("utf-8")
    assert "脱敏" in notice
    assert "复核" in notice, "没有提醒用户发送前复核 —— 脱敏不保证 100% 覆盖"
