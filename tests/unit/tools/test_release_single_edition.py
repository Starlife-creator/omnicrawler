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


def test_full_build_is_no_longer_requested() -> None:
    """三平台构建脚本**不得**再请求 Full（2026-11-19 维护者裁定）。

    判据变了：早先要求「停发不停建」（照常编译 Full），现在改为**不构建**。
    理由是每平台多跑一轮完整 PyInstaller（Linux 实测多约 40 分钟），
    而它证明的「重依赖共存」并非用户路径 —— 用户拿的是 Standard + 按需装组件。
    """
    for name in ("reusable-build-windows.yml", "reusable-build-linux.yml", "reusable-build-macos.yml"):
        path = _REPO / ".github" / "workflows" / name
        if not path.is_file():
            pytest.skip(f"缺少 {name}")
        text = path.read_text(encoding="utf-8", errors="replace")
        for needle in ("-Edition Full", "--edition Full"):
            assert needle not in text, f"{name} 仍然在构建 Full（{needle!r}）"


def test_component_verification_replaces_capability_comparison() -> None:
    """取代关系必须成立：旧对账下线，新验证接线。

    这是同一个判据的两面：只检查"旧的没了"会让新流程漏接也看不出来。
    """
    release = _REPO / ".github" / "workflows" / "release.yml"
    text = release.read_text(encoding="utf-8", errors="replace")

    assert "compare-capabilities" not in text, "旧的等价比对步骤还在"
    assert "verify_standard_components.py" in text, "新的组件验证步骤没有接线"


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

def test_budget_table_has_no_full_entry() -> None:
    """预算表不得再声明 Full。

    `check_artifact_budget.py` 会要求「声明的每个 edition 都必须有产物」。
    保留 Full 条目会让「不再构建 Full」之后每个平台都报
    `missing <platform> Full portable artifact` —— 判据在要求一个不生产的产物。
    """
    import json

    path = _REPO / "packaging" / "artifact-budgets.json"
    if not path.is_file():
        pytest.skip("需要 artifact-budgets.json")

    data = json.loads(path.read_text(encoding="utf-8"))
    offenders = [plat for plat, eds in data.items() if isinstance(eds, dict) and "Full" in eds]
    assert not offenders, f"预算表仍声明 Full：{offenders}"


def test_budget_gate_iterates_declared_editions() -> None:
    """门禁必须按预算表声明遍历，不得硬编码 edition 列表。

    写死 `("Standard", "Full")` 是那个真实缺陷的根因：删掉 Full 构建之后
    CI 三平台全部报 `missing ... Full portable artifact`。
    """
    gate = _REPO / "tools" / "gates" / "check_artifact_budget.py"
    if not gate.is_file():
        pytest.skip("需要 check_artifact_budget.py")

    source = gate.read_text(encoding="utf-8")
    assert 'for edition in ("Standard", "Full")' not in source, (
        "门禁仍硬编码 Standard/Full —— 会在不支持 Full 时误报缺失"
    )
    assert "for edition in editions" in source, "门禁未按声明列表遍历"

def test_checksums_do_not_require_full_artifact() -> None:
    """校验和生成器不得再要求 Full 产物。

    它有一张 `_PLATFORM_REQUIRED` 表，早先每个平台列了 Standard + Full 两件。
    删掉 Full 构建后，这一步会因「以下类型的发行物未包含：...-Full.tar.xz」
    而 `exit code 2`（真实 CI 实测）。
    """
    tool = _REPO / "tools" / "releasing" / "generate_checksums.py"
    if not tool.is_file():
        pytest.skip("需要 generate_checksums.py")

    source = tool.read_text(encoding="utf-8")
    offenders = [
        line.strip()
        for line in source.splitlines()
        if "Portable-Full" in line and not line.strip().startswith("#")
    ]
    assert not offenders, f"校验和要求 Full 产物：{offenders}"
