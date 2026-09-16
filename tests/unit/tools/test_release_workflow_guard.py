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
