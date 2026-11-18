"""「停发不停建」：Full 照常构建，但不挂公开 Release。

## 为什么这么定

维护者明确：

> full 构建怎么能建议直接删除呢，他是我迭代了很多轮的结果，你可以修改他
> 让他适配现状作为全量测试的基准，但是不能删掉。

`Full` 是 PyInstaller 冻结兼容性的**试金石**：`paddle` / `cv2` / `lxml` /
`PySide6` / `playwright` 同处冻结环境时的 hooks、动态库收集与命名空间包冲突，
只有全量构建能第一时间暴露。删了它，后续升级 Python 或基础库就失去
自动化回归基准。

但对外只发**单一 Standard 包**，消除用户面对双版本的选择困惑。

## 判据（本文件只验证**配置意图**，不验证真实构建产物）

1. 挂载循环必须排除 Full 产物（`*-Portable-Full.*`）；
2. Standard 产物必须仍被发布；
3. 排除必须发生在**挂载阶段**，不能删掉构建步骤——构建照跑才有回归证据；
4. 排除规则必须覆盖三平台（Windows .zip / Linux .tar.xz / macOS .dmg）。

## 明确的验证边界

**本机无法验证**：真实 PyInstaller 构建、三平台冒烟、Release 实际挂载结果。
这些只能由 CI 跑完后确认。本文件验证的是"工作流配置表达了正确的意图"。
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_FINALIZE = _REPO / ".github" / "workflows" / "reusable-finalize-release.yml"
_RELEASE = _REPO / ".github" / "workflows" / "release.yml"

pytestmark = pytest.mark.skipif(
    not _FINALIZE.is_file(), reason="需要 .github/workflows"
)

#: 假设的产物名（按 build 脚本的实际命名规则构造）
_SAMPLE_FILES = {
    "windows_standard": "OmniCrawler-1.0.2-Windows-Portable-Standard.zip",
    "windows_full": "OmniCrawler-1.0.2-Windows-Portable-Full.zip",
    "linux_standard": "OmniCrawler-1.0.2-Linux-Portable-Standard.tar.xz",
    "linux_full": "OmniCrawler-1.0.2-Linux-Portable-Full.tar.xz",
    "macos_standard": "OmniCrawler-1.0.2-macOS-Portable-Standard.dmg",
    "macos_full": "OmniCrawler-1.0.2-macOS-Portable-Full.dmg",
}


def _finalize_text() -> str:
    return _FINALIZE.read_text(encoding="utf-8")


def _exclusion_patterns() -> list[str]:
    """从挂载循环里提取 `case` 的排除模式。"""
    text = _finalize_text()
    patterns: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("OmniCrawler-*-Full.") or "Portable-Full" in stripped:
            head = stripped.split(")")[0]
            patterns.extend(part for part in head.split("|") if part.strip())
    return patterns


def test_finalize_workflow_is_parseable_yaml() -> None:
    """工作流必须是合法 YAML（改坏了 CI 会整个不跑）。"""
    yaml = pytest.importorskip("yaml")
    yaml.safe_load(_finalize_text())


def test_full_artifacts_are_excluded_from_release() -> None:
    """Full 产物必须被排除在公开 Release 之外。"""
    patterns = _exclusion_patterns()
    assert patterns, (
        "没找到排除 Full 的模式 —— 停发不停建未生效，Full 仍会挂到公开 Release"
    )
    for key in ("windows_full", "linux_full", "macos_full"):
        name = _SAMPLE_FILES[key]
        matched = any(fnmatch.fnmatchcase(name, p) for p in patterns)
        assert matched, f"{name} 未被排除（模式 {patterns}）"


def test_standard_artifacts_are_still_published() -> None:
    """Standard 产物必须仍被发布——排除规则不能误伤。"""
    patterns = _exclusion_patterns()
    for key in ("windows_standard", "linux_standard", "macos_standard"):
        name = _SAMPLE_FILES[key]
        matched = any(fnmatch.fnmatchcase(name, p) for p in patterns)
        assert not matched, f"{name} 被误排除了（模式 {patterns}）"


def test_exclusion_happens_in_mount_loop_not_build() -> None:
    """排除必须在**挂载阶段**，不能靠删掉构建步骤实现。

    构建照跑才有冻结兼容性的回归证据。
    """
    text = _finalize_text()
    # 找到 for f in * 之后的 case 块
    loop_index = text.find("for f in *; do")
    assert loop_index > 0, "没找到挂载循环"
    after_loop = text[loop_index : loop_index + 2000]
    assert "Portable-Full" in after_loop or "*-Full." in after_loop, (
        "排除规则不在挂载循环里"
    )


def test_build_step_still_builds_full() -> None:
    """构建侧必须仍然编 Full（停发不停建的关键一半）。"""
    windows = _REPO / ".github" / "workflows" / "reusable-build-windows.yml"
    if not windows.is_file():
        pytest.skip("需要 build 工作流")

    text = windows.read_text(encoding="utf-8")
    assert "-Edition Full" in text, (
        "构建侧不再编 Full —— 那就成了「停发也停建」，丢掉了冻结兼容性回归基准"
    )


def test_smoke_test_still_checks_full() -> None:
    """冒烟测试必须仍然验 Full（它是回归基准的价值所在）。"""
    text = _RELEASE.read_text(encoding="utf-8")
    assert "--edition Full" in text, (
        "冒烟不再验 Full —— 停发之后就没有任何机制保证 Full 还能跑"
    )


def test_exclusion_has_explanatory_comment() -> None:
    """排除规则必须写明理由，否则后来者会当它是遗漏而"修好"。"""
    text = _finalize_text()
    loop_index = text.find("for f in *; do")
    before_loop = text[max(0, loop_index - 2000) : loop_index]
    assert "停发不停建" in before_loop or "停发" in before_loop, (
        "排除规则缺少理由说明 —— 后来者可能把它当 bug 删掉"
    )
    assert "试金石" in before_loop or "冻结" in before_loop, (
        "未说明为什么不删 Full（它是冻结兼容性基准）"
    )
