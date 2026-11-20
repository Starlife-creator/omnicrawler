"""`component-probe` 的构建链判断与 workflow 契约守卫。

## 为什么这批守卫重要

维护者 2026-11-20 指出过一个真实风险：

> 比如说我们这次优化了构建，那你直接复用上次构建产物，岂不是就测试不了
> 你这次的结果了吗？

**复用旧产物只能验证运行期变化**。如果本轮改的是构建链（spec / 打包脚本 /
`runtime_paths` / 依赖锁），旧产物里根本不含这些改动 —— 复用它等于什么都没测，
却会给出「通过」的假象。

所以这批守卫钉住三件事：

1. 构建链路径**必须**判成 `build`；
2. 运行期路径可以判成 `reuse`；
3. **无法归类时必须判 `build`**（保守），且 workflow 不得绕过这个判断。
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
sys_path_added = False


def _classify(paths: list[str]) -> tuple[str, str, list[str]]:
    import sys

    global sys_path_added
    if not sys_path_added:
        sys.path.insert(0, str(_REPO / "tools" / "portable"))
        sys_path_added = True
    from needs_rebuild import classify

    return classify(paths)


@pytest.mark.parametrize(
    "path",
    [
        "build_linux.sh",
        "build_windows.ps1",
        "build_macos.sh",
        "packaging/artifact-budgets.json",
        "pyproject.toml",
        "uv.lock",
        "constraints/quality.txt",
        "src/omnicrawler/core/runtime_paths.py",
        "tools/builders/create_runtime_manifest.py",
        "tools/prepare_windows_runtime.ps1",
    ],
)
def test_build_chain_paths_force_rebuild(path: str) -> None:
    """触及构建链 ⇒ **必须**重建（旧产物不含这些改动）。"""
    decision, reason, triggers = _classify([path])
    assert decision == "build", f"{path} 被判成 reuse，会测不到本轮改动"
    assert triggers or "无法" in reason


@pytest.mark.parametrize(
    "path",
    [
        "market/components/ocr-paddle/recipe.json",
        "src/omnicrawler/services/component_worker.py",
        "src/omnicrawler/services/component_runner.py",
        "src/omnicrawler/services/component_smoke.py",
        "tools/gates/check_component_recipes.py",
        "tests/unit/services/test_component_smoke.py",
        "docs/COMPONENTS.md",
    ],
)
def test_runtime_only_paths_allow_reuse(path: str) -> None:
    """只在运行期/组件层 ⇒ 可复用（构建产物未变）。"""
    decision, _reason, _triggers = _classify([path])
    assert decision == "reuse", f"{path} 被判成 build，白花 40 分钟"


def test_unknown_path_forces_rebuild() -> None:
    """**无法归类必须保守重建** —— 宁可多花时间，也不给假通过。"""
    decision, reason, _ = _classify(["some/never/heard/of/this.xyz"])
    assert decision == "build"
    assert "无法归类" in reason or "保守" in reason


def test_empty_change_list_forces_rebuild() -> None:
    """改动列表为空 ⇒ 判断不了，保守重建。"""
    decision, _reason, _ = _classify([])
    assert decision == "build"


def test_mixed_changes_force_rebuild() -> None:
    """只要有一个构建链文件，整体就重建。"""
    decision, _, triggers = _classify(
        ["market/components/ocr-paddle/recipe.json", "build_macos.sh"]
    )
    assert decision == "build"
    assert "build_macos.sh" in triggers


def test_probe_workflow_honours_decision() -> None:
    """workflow 必须真的按 `decision` 分叉，不能无脑复用。"""
    workflow = _REPO / ".github" / "workflows" / "component-probe.yml"
    if not workflow.is_file():
        pytest.skip("需要 component-probe.yml")

    text = workflow.read_text(encoding="utf-8")
    assert "needs_rebuild.py" in text, "workflow 没有调用构建链判断"
    assert "needs.decide.outputs.decision == 'build'" in text, "缺少重建分支"
    assert "needs.decide.outputs.decision == 'reuse'" in text, "缺少复用分支"
    # 复用失败必须有重建兜底 —— 否则「取不到上次产物」会变成跳过验证
    assert "build-fallback" in text, "复用失败时没有重建兜底"


def test_probe_workflow_does_not_create_tags_or_releases() -> None:
    """按维护者裁定：**不拿版本号试错**，探针不得创建 tag 或 release。"""
    workflow = _REPO / ".github" / "workflows" / "component-probe.yml"
    if not workflow.is_file():
        pytest.skip("需要 component-probe.yml")

    text = workflow.read_text(encoding="utf-8")
    for forbidden in ("git tag", "git push --tags", "gh release create"):
        assert forbidden not in text, f"探针工作流出现 {forbidden!r} —— 违反不消耗版本号的裁定"
