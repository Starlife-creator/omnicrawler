"""工作流 ↔ 构建脚本之间的**接线契约**（发布链上最容易被静默改坏的一段）。

为什么需要它：变更包的基线窗口是**四跳**传下去的 ——

    release.yml（从 tag 推导）
      → workflow_call 的输入 `delta_baselines` / `release_url_base`
        → reusable-build-*.yml 的 `env:`（**名字必须与脚本读的一致**）
          → build_*.sh / build_windows.ps1 里的 `--baselines` / `--baseline-url-base`
            → tools/build_update_delta.py

**任何一跳改名漏改，症状都是"静默地没有变更包"**（脚本走 `|| WARN` 分支、构建照样成功），
要到用户抱怨"小版本还是下了整包"才发现。而它只在**真发布**时才被跑到 ⇒ 常规单测照不到。

所以这里把四跳的**字面名字**钉成一条契约：改名必须同批改，否则这个用例红。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
RELEASE = WORKFLOWS / "release.yml"
REUSABLE = {
    "windows": WORKFLOWS / "reusable-build-windows.yml",
    "linux": WORKFLOWS / "reusable-build-linux.yml",
    "macos": WORKFLOWS / "reusable-build-macos.yml",
}
BUILD_SCRIPTS = {
    "windows": REPO_ROOT / "build_windows.ps1",
    "linux": REPO_ROOT / "build_linux.sh",
    "macos": REPO_ROOT / "build_macos.sh",
}

#: 环境变量名（工作流 env ↔ 脚本读取之间的**唯一**约定）。改名必须同批改这四处。
ENV_BASELINES = "OMNICRAWL_DELTA_BASELINES"
ENV_URL_BASE = "OMNICRAWL_RELEASE_URL_BASE"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_release_workflow_declares_and_exports_the_baseline_inputs() -> None:
    """① release.yml 必须**先声明输出**（`delta_baselines` / `release_url_base`）。

    少了这一步，`needs.<job>.outputs.delta_baselines` 会解析成空串 ⇒ 三平台都拿不到基线
    ⇒ 又一次"静默没有变更包"。所以这里同时钉"声明了输出"与"从 tag 推导时写出了它们"。
    """
    text = _text(RELEASE)

    assert re.search(r"^\s*delta_baselines:\s*\$\{\{\s*steps\.\S+\.outputs\.delta_baselines", text, re.M)
    assert re.search(r"^\s*release_url_base:\s*\$\{\{\s*steps\.\S+\.outputs\.release_url_base", text, re.M)
    assert 'echo "delta_baselines=' in text, "必须真的往 GITHUB_OUTPUT 写这个值"
    assert 'echo "release_url_base=' in text, "必须真的往 GITHUB_OUTPUT 写这个值"
    # 基线来自**版本 tag**（不是分支、不是 release 名）
    assert "python tools/release_delta_baselines.py" in text
    helper = _text(REPO_ROOT / "tools" / "release_delta_baselines.py")
    assert '"tag", "--list"' in helper
    assert "limit: int = 3" in helper, "窗口大小 K=3 要能一眼看见"


def test_release_workflow_forwards_both_inputs_to_every_build_job() -> None:
    """② 三个 build job 都要把两个输出**转发**成 workflow_call 输入（漏一个 = 那个平台没有变更包）。"""
    text = _text(RELEASE)
    # ★ 断言**完整表达式**（含 `outputs.` 之后的那个名字）：只查前缀 `outputs.` 的话，
    #   右半段写错（`outputs.da_baselines`）照样匹配 —— 反向断言实测漏过一次。
    forward_baselines = text.count(
        "delta_baselines: ${{ needs.verify-python-version.outputs.delta_baselines }}"
    )
    forward_url = text.count(
        "release_url_base: ${{ needs.verify-python-version.outputs.release_url_base }}"
    )

    assert forward_baselines == 3, f"delta_baselines 只转发了 {forward_baselines} 次（应 3 次）"
    assert forward_url == 3, f"release_url_base 只转发了 {forward_url} 次（应 3 次）"


@pytest.mark.parametrize("platform", sorted(REUSABLE))
def test_reusable_build_workflow_declares_inputs_and_env(platform: str) -> None:
    """③ 每个 reusable 工作流都要：声明输入 → 导出成**脚本读得到的环境变量名**。"""
    text = _text(REUSABLE[platform])

    for name in ("delta_baselines", "release_url_base"):
        assert re.search(rf"^\s+{name}:$", text, re.M), f"{platform}: 缺输入 {name}"

    assert re.search(rf"^\s*{ENV_BASELINES}:\s*\$\{{\{{\s*inputs\.delta_baselines", text, re.M), (
        f"{platform}: {ENV_BASELINES} 没有接到 inputs.delta_baselines"
    )
    assert re.search(rf"^\s*{ENV_URL_BASE}:\s*\$\{{\{{\s*inputs\.release_url_base", text, re.M), (
        f"{platform}: {ENV_URL_BASE} 没有接到 inputs.release_url_base"
    )


@pytest.mark.parametrize("platform", sorted(BUILD_SCRIPTS))
def test_build_script_reads_the_same_env_names(platform: str) -> None:
    """④ 构建脚本必须读**同名**环境变量，并把它交给变更包工具。"""
    text = _text(BUILD_SCRIPTS[platform])

    # ★ 不能只查"名字出现过"：脚本里这个名字出现**两次**（守空分支 + 传参），
    #   只改一处仍然"命中" —— 反向断言实测漏过一次。两处用法都要在。
    if platform == "windows":
        guard = f"if ($env:{ENV_BASELINES})"
        passed = f"--baselines $env:{ENV_BASELINES}"
        url_passed = f"--baseline-url-base $env:{ENV_URL_BASE}"
    else:
        guard = f'if [[ -n "${{{ENV_BASELINES}:-}}" ]]'
        passed = f'--baselines "${ENV_BASELINES}"'
        url_passed = f'--baseline-url-base "${ENV_URL_BASE}"'
    assert guard in text, f"{platform}: 没有对空基线做分支（{guard}）"
    assert passed in text, f"{platform}: 没把 {ENV_BASELINES} 交给变更包工具（{passed}）"
    assert url_passed in text, f"{platform}: 没把 {ENV_URL_BASE} 交给变更包工具（{url_passed}）"
    # 传下去的开关名要与工具的参数一致（工具侧由 test_update_delta_tool 钉）
    assert "--baselines" in text, f"{platform}: 没把基线交给变更包工具"
    assert "--baseline-url-base" in text, f"{platform}: 没把发布基址交给变更包工具"
    # 两个"参数文件"是构建脚本与清单工具之间的接口
    assert "--emit-args" in text and "--emit-previous-args" in text
    assert "--delta-file" in text and "--previous-manifest-file" in text
