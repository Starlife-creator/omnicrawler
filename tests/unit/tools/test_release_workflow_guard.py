"""派发 `release.yml` 时**不得建 Release**（W4.1 / 决策 #3）。

## 为什么需要这条守卫

`release.yml` 同时支持 tag 推送与无参 `workflow_dispatch`（决策 #3 允许手工派发，
但**只留 Actions artifacts、不建 Release**）。而它的 `release` job 会调
`reusable-finalize-release.yml` → `softprops/action-gh-release` **建 Release**，
且那个 reusable workflow 自身**没有**事件守卫 ——

⇒ 只要有人在 `release.yml` 里删掉 `release` job 的 `if:`，一次手工派发就会**建出 Release**。
本文件把这条守卫钉住（含"reusable 里确实有建 Release 的动作"这一前提，避免守卫空转）。
"""

from __future__ import annotations

from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RELEASE = _REPO_ROOT / ".github" / "workflows" / "release.yml"
_FINALIZE = _REPO_ROOT / ".github" / "workflows" / "reusable-finalize-release.yml"


def _workflow(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_dispatch_trigger_exists_and_release_job_is_guarded() -> None:
    data = _workflow(_RELEASE)
    # 前提一：确实支持手工派发（否则本守卫没有意义）
    triggers = data[True] if True in data else data.get("on")
    assert "workflow_dispatch" in triggers, f"release.yml 未声明 workflow_dispatch：{triggers}"

    release_job = (data.get("jobs") or {}).get("release") or {}
    condition = str(release_job.get("if") or "")
    assert "startsWith(github.ref, 'refs/tags/" in condition, (
        "`release` job 缺少“只在 tag 推送时执行”的守卫 ⇒ 手工派发会建 Release（决策 #3 不允许）。"
        f"当前 if={condition!r}"
    )


def test_finalize_really_creates_a_release() -> None:
    """前提二：finalize 里**确实**有建 Release 的动作 —— 否则上面的守卫是空转。"""
    text = _FINALIZE.read_text(encoding="utf-8")
    assert "action-gh-release" in text, "finalize 里没有建 Release 的动作，守卫的前提不成立"


def test_guard_is_not_vacuous_for_other_jobs() -> None:
    """三个平台构建 job 必须**不加**该守卫（手工派发正是为了拿它们的产物）。"""
    data = _workflow(_RELEASE)
    for name in ("build-windows-portable", "build-linux-portable", "build-macos-portable"):
        job = (data.get("jobs") or {}).get(name) or {}
        assert job, f"缺少构建 job {name}"
        assert "startsWith(github.ref" not in str(job.get("if") or ""), (
            f"{name} 被加了 tag 守卫 ⇒ 手工派发将拿不到产物，与决策 #3 的意图相反"
        )


# ── ★ W4.2：归档级冒烟 job（L0.5 闸门） ─────────────────────────────────────


def test_portable_smoke_job_exists_and_is_dispatched_too() -> None:
    """`portable-smoke` 必须在 `release.yml` 里，且**不能**带 tag 守卫。

    它正是我们"手工派发验证产物可用"的手段 ⇒ 加了 tag 守卫就等于只在发布时才发现产物坏掉。
    """
    data = _workflow(_RELEASE)
    job = (data.get("jobs") or {}).get("portable-smoke") or {}
    assert job, "release.yml 缺少 W4.2 的 `portable-smoke` job"
    assert "startsWith(github.ref" not in str(job.get("if") or ""), (
        "`portable-smoke` 被加了 tag 守卫 ⇒ 手工派发不再验证产物可用性"
    )


def test_portable_smoke_needs_every_build_and_downloads_its_artifact() -> None:
    """它必须依赖**全部三个**平台构建 job，并真的去**下载**产物（而不是在构建树上跑）。"""
    data = _workflow(_RELEASE)
    job = (data.get("jobs") or {}).get("portable-smoke") or {}
    needs = job.get("needs") or []
    if isinstance(needs, str):
        needs = [needs]
    for name in (
        "build-windows-portable",
        "build-linux-portable",
        "build-macos-portable",
        "verify-python-version",
    ):
        assert name in needs, f"`portable-smoke` 缺少 needs: {name}（防止在产物不存在时假通过）"

    text = _RELEASE.read_text(encoding="utf-8")
    assert "actions/download-artifact@" in text, (
        "`portable-smoke` 必须**下载已上传的归档**再跑 —— 在构建树上跑就退化成构建期已有的检查，"
        "归档往返（pack→upload→download→unpack）依旧无人验证"
    )
    assert "tools/portable_archive_smoke.py" in text, "没接线到 W4.2 的工具"
    # 三平台都要在矩阵里，否则"三平台产物可用"仍然只是声称
    for platform in ("linux", "windows", "macos"):
        assert platform in text, f"`portable-smoke` 矩阵缺少 platform={platform}"


def test_release_is_blocked_by_the_archive_smoke() -> None:
    """★ `release` 必须把 `portable-smoke` 列入 `needs` ⇒ **产物不可用就不发布**。"""
    data = _workflow(_RELEASE)
    needs = (data.get("jobs") or {}).get("release", {}).get("needs") or []
    if isinstance(needs, str):
        needs = [needs]
    assert "portable-smoke" in needs, (
        "`release` 没有依赖 `portable-smoke` ⇒ 归档坏掉时仍会照常发布（W4.2 的闸门形同虚设）"
    )
