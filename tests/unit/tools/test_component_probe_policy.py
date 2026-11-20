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
    # 决策通过 run_build 传给 reusable（不能用 uses+if，见 test_no_uses_job_has_if）
    assert "run_build:" in text, "没有把决策传给构建开关"
    assert "decision == 'build'" in text, "run_build 表达式没有引用决策"
    # 复用失败必须有重建兜底 —— 否则「取不到上次产物」会变成跳过验证
    assert "found != 'true'" in text, "复用失败时没有触发重建"


def test_probe_workflow_does_not_create_tags_or_releases() -> None:
    """按维护者裁定：**不拿版本号试错**，探针不得创建 tag 或 release。"""
    workflow = _REPO / ".github" / "workflows" / "component-probe.yml"
    if not workflow.is_file():
        pytest.skip("需要 component-probe.yml")

    text = workflow.read_text(encoding="utf-8")
    for forbidden in ("git tag", "git push --tags", "gh release create"):
        assert forbidden not in text, f"探针工作流出现 {forbidden!r} —— 违反不消耗版本号的裁定"

def test_github_output_is_single_line() -> None:
    """`--github-output` 的每一行必须是 `key=value`，**值里不能有换行**。

    **真实 CI 踩到**：`reason` 含中文括号与破折号且被折行，写进 `$GITHUB_OUTPUT`
    后把后续行当成新 key，导致 `decision` 变成空值 —— `run_build` 表达式失效，
    构建该跳过时没跳过、该跑时没跑。
    """
    import subprocess
    import sys

    from tools.child_env import utf8_env

    completed = subprocess.run(
        [
            sys.executable,
            str(_REPO / "tools" / "portable" / "needs_rebuild.py"),
            "--paths",
            "build_linux.sh",
            "--github-output",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(_REPO),
        env=utf8_env(),
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0
    lines = [ln for ln in (completed.stdout or "").splitlines() if ln.strip()]
    assert lines, "没有输出"
    for line in lines:
        assert "=" in line, f"不是 key=value 形式：{line!r}"
        key, _, value = line.partition("=")
        assert key in {"decision", "reason"}, f"未预期的 key：{key!r}"
        assert value, f"{key} 的值为空"
    keys = {ln.partition("=")[0] for ln in lines}
    assert keys == {"decision", "reason"}, f"缺少字段：{keys}"


def test_workflow_declares_attestation_permissions() -> None:
    """调用 reusable 构建的工作流**必须**声明 id-token / attestations。

    **真实踩到**：`component-probe.yml` 少了这两个权限，GitHub 在**解析阶段**
    就拒绝整个 workflow，表现为 `startup_failure` 且 jobs=0、**没有任何日志**。
    用 5 轮二分才定位到（排除 with 表达式、排除 run_build 开关）。
    """
    workflow = _REPO / ".github" / "workflows" / "component-probe.yml"
    if not workflow.is_file():
        pytest.skip("需要 component-probe.yml")

    import yaml

    data = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    perms = data.get("permissions") or {}
    assert perms.get("id-token") == "write", "缺少 id-token: write（会导致 startup_failure）"
    assert perms.get("attestations") == "write", "缺少 attestations: write"


def test_no_uses_job_has_if() -> None:
    """`uses:` 调用的 job **不得**带 `if:` —— GitHub 解析阶段直接拒绝。

    条件开关必须放进被调用方内部（`run_build` 输入 + 它自己的 job 级 if）。
    """
    import yaml

    for name in ("component-probe.yml",):
        path = _REPO / ".github" / "workflows" / name
        if not path.is_file():
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        offenders = [
            job_name
            for job_name, job in (data.get("jobs") or {}).items()
            if "uses" in job and "if" in job
        ]
        assert not offenders, f"{name} 里这些 job 同时有 uses 与 if：{offenders}"


def test_probe_workflow_paths_are_classified() -> None:
    """探针自身与 release 链路必须能正确归类，不留「无法归类」。"""
    assert _classify([".github/workflows/component-probe.yml"])[0] == "reuse"
    assert _classify([".github/workflows/release.yml"])[0] == "build"
    assert _classify([".github/workflows/reusable-build-linux.yml"])[0] == "build"
