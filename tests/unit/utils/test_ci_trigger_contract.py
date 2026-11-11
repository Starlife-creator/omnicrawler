from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = (
    "quality.yml",
    "e2e.yml",
    "plugin-sandbox.yml",
    "license-gate.yml",
)


def test_expensive_ci_does_not_run_twice_for_internal_pull_requests() -> None:
    """Feature pushes are covered by the PR event; direct main pushes remain covered."""
    workflow_root = PROJECT_ROOT / ".github" / "workflows"
    for name in WORKFLOWS:
        workflow = (workflow_root / name).read_text(encoding="utf-8")
        assert "  push:\n    branches: [main]" in workflow, name
        assert "  pull_request:" in workflow, name


def test_quality_collects_platform_failures_without_masking_them() -> None:
    import yaml

    workflow = yaml.safe_load((PROJECT_ROOT / ".github/workflows/quality.yml").read_text(encoding="utf-8"))
    job = workflow["jobs"]["test"]
    assert job["strategy"]["fail-fast"] is False
    steps = job["steps"]
    test = next(step for step in steps if step.get("id") == "core_tests")
    assert not test.get("continue-on-error", False)
    assert "--junitxml=artifacts/ci/pytest-core.xml" in test["run"]
    coverage = next(step for step in steps if step.get("id") == "core_coverage")
    assert "steps.core_tests.outcome == 'failure'" in coverage["if"]
    gate = next(step for step in steps if "--profile core coverage.json" in step.get("run", ""))
    assert "steps.core_coverage.outcome == 'success'" in gate["if"]
    assert not gate.get("continue-on-error", False)
    report = next(step for step in steps if step.get("name") == "Preserve core test and coverage diagnostics")
    assert "steps.core_tests.outcome == 'failure'" in report["if"]
    assert "coverage.json" in report["with"]["path"]
    assert "pytest-core.xml" in report["with"]["path"]


def _pytest_runs(workflow: dict) -> list[str]:
    """工作流里所有真正执行用例的 pytest 命令行（排除 ``--collect-only`` 的收集步）。"""
    runs: list[str] = []
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            run = step.get("run", "")
            if "pytest" not in run or "--collect-only" in run:
                continue
            runs.append(run)
    return runs


def test_every_ci_pytest_run_arms_faulthandler_timeout() -> None:
    """CI 的每个 pytest 执行步都必须带 ``-o faulthandler_timeout``。

    失败形态（2026-11-11 实测）：main 上 ``test`` 作业连续 5 次在 45 分钟
    ``timeout-minutes`` 处被取消，日志只有 ``[1%]`` + 43 分钟零输出——没有栈就
    无法知道挂在哪条用例，只能靠猜。``-o faulthandler_timeout=300`` 让单条用例
    超过 5 分钟就把调用栈打进日志，把「黑箱超时」变成「可定位的挂起点」。
    它只加可观测性、不改判定（超时仍由作业级 ``timeout-minutes`` 兜底）。
    """
    import yaml

    workflow_root = PROJECT_ROOT / ".github" / "workflows"
    offenders: list[str] = []
    for path in sorted(workflow_root.glob("*.yml")):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(workflow, dict) or "jobs" not in workflow:
            continue
        for run in _pytest_runs(workflow):
            if "-o faulthandler_timeout=" not in run:
                offenders.append(f"{path.name}: {run.strip().splitlines()[0][:120]}")
    assert offenders == [], "以下 CI pytest 执行步缺少 faulthandler 超时：" + "; ".join(offenders)


def test_faulthandler_guard_detects_a_step_that_drops_the_flag() -> None:
    """反向断言：守卫必须真的能判红。

    把带超时的命令去掉 ``-o faulthandler_timeout=…`` 再喂给同一个判据，必须被
    识别为缺失。没有这条，上面那条测试在被改成恒真时也不会有人发现。
    """
    missing = {
        "jobs": {
            "test": {
                "steps": [
                    {"id": "core_tests", "run": "python -m coverage run -m pytest -q --junitxml=x.xml"},
                ]
            }
        }
    }
    assert _pytest_runs(missing) != []
    assert not any(
        "-o faulthandler_timeout=" in run for run in _pytest_runs(missing)
    ), "反向断言失效：判据没有识别出缺失超时标志的命令"
    collect_only = {
        "jobs": {"test": {"steps": [{"run": "python -m pytest --collect-only -q tests"}]}}
    }
    assert _pytest_runs(collect_only) == [], "收集步不该被当成执行步"


def test_e2e_runner_also_arms_faulthandler_timeout() -> None:
    """``e2e/run.py`` 自己在子进程里跑 pytest，同样必须带 ``-o faulthandler_timeout``。

    那个文件不在 workflow YAML 里，前面的守卫看不见它；E2E 作业超时取消时同样
    需要一个栈来区分「用例挂起」和「浏览器永久等待」。
    """
    source = (PROJECT_ROOT / "e2e" / "run.py").read_text(encoding="utf-8")
    assert '_FAULTHANDLER = ("-o", "faulthandler_timeout=300")' in source
    # 两个执行点：`--full-regression` 的全量回归与带 coverage 的 e2e 套件。
    assert source.count("*_FAULTHANDLER,") == 2
