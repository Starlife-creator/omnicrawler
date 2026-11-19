"""「装齐组件后验证可运行」的守卫（取代原先的能力等价性对账）。

## 为什么改方向（2026-11-19 维护者裁定）

> 有必要对账吗，是不是修改一下 full 构建逻辑测试出来它的测试没问题就行，
> 主要测试装完所有组件的兼容性等。没必要构建一个和之前的一样的大号冻结包。

原做法有两处不合算：

1. **Full 大号冻结包**：每平台多跑一轮 PyInstaller（Linux 实测多约 40 分钟），
   而它证明的「重依赖能装上并跑通」并非用户真实路径；
2. **能力等价性对账**：拿两份静态能力 JSON 比数字，报出的缺口大多是
   `full` 档能力（`paddle_models` / `selenium` / `crawl4ai` / `s3`）——
   那些本就不该由 Standard 承担。比数字证明不了「装完能用」。

现判据：**逐个 install-recipe --download，再逐个 components run 跑起来**。

## 本文件测什么

* `verify_standard_components` 的编排与失败路径（空列表 / 缺 catalog / 缺 recipe）；
* 无 PYTHONPATH 下直接执行（CI 就是这样调它的）；
* **旧的对账流程必须已下线**（防止有人把旧流程装回去）。

历史模块 `standard_equivalence.py` / `collect_capability_reports.py` 保留在仓库里
（项目纪律不用 `git rm`），但不再接入任何 workflow。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_VERIFY = _REPO / "tools" / "portable" / "verify_standard_components.py"
_WORKFLOWS = _REPO / ".github" / "workflows"


def _load_verify():
    import importlib.util

    spec = importlib.util.spec_from_file_location("_verify_under_test", _VERIFY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def verify():
    if not _VERIFY.is_file():
        pytest.skip("需要 verify_standard_components.py")
    return _load_verify()


# ── 编排逻辑 ─────────────────────────────────────────────────────────────
def test_empty_component_list_is_a_failure(verify, tmp_path: Path) -> None:
    """组件列表为空必须判失败 —— 空集合会让「全部通过」恒真。"""
    market = tmp_path / "market"
    market.mkdir()
    (market / "catalog.json").write_text(json.dumps({"components": []}), encoding="utf-8")

    report = verify.verify_all(
        executable=tmp_path / "fake-cli",
        release_root=tmp_path,
        market_root=market,
    )
    assert not report.ok
    assert not report.installed


def test_missing_catalog_is_a_failure(verify, tmp_path: Path) -> None:
    report = verify.verify_all(
        executable=tmp_path / "fake",
        release_root=tmp_path,
        market_root=tmp_path / "no-such-market",
    )
    assert not report.ok
    assert report.failures


def test_missing_recipe_fails_with_component_name(verify, tmp_path: Path) -> None:
    """recipe 缺失要**指名是哪个组件**，不能只说"失败了"。"""
    market = tmp_path / "market"
    (market / "components").mkdir(parents=True)
    (market / "catalog.json").write_text(
        json.dumps(
            {"components": [{"id": "ghost", "recipe_file": "components/ghost/recipe.json"}]}
        ),
        encoding="utf-8",
    )
    report = verify.verify_all(
        executable=tmp_path / "fake",
        release_root=tmp_path,
        market_root=market,
    )
    assert not report.ok
    assert any(cid == "ghost" for cid, _stage, _why in report.failures)
    assert any(stage == "安装" for _cid, stage, _why in report.failures)


def test_tail_prefers_end_of_output(verify) -> None:
    """错误详情取**末尾**，因为警告通常在开头。

    v1.0.7 踩过：取前 400 字符，把 `内置 Tesseract 缺失` 这条 warning
    误当成了 ocr-paddle 的失败原因，真实原因被截断。
    """
    # 警告足够多，末尾 400 字符里应几乎全是错误
    text = "WARNING: 无关警告\n" * 200 + "ERROR: 真正的原因"
    detail = verify._tail(text)
    assert "真正的原因" in detail, "末尾的真实错误被截掉了"
    # 判据是**占比**而非「一个字都不能有」：末尾窗口里必然带着几行前面的警告，
    # 要求纯净会把阈值推向"更长就丢"的错误方向。
    # 判据：错误在**最后**，且前面的警告被截掉了大半（200 行只剩少量）
    assert detail.rstrip().endswith("真正的原因"), "错误不在末尾"
    # 窗口 600 字符 ÷ 每行约 14 字符 ≈ 42 行；原文 200 行 ⇒ 必须远小于原量
    assert detail.count("无关警告") < 60, (
        f"末尾窗口里塞了 {detail.count('无关警告')} 行警告（原文 200 行），"
        "说明取的不是末尾"
    )

    # 短输出时整段保留（不能为了取末尾而丢信息）
    short = "ERROR: 短输出的错误"
    assert verify._tail(short) == short


# ── 无 PYTHONPATH 直接执行（CI 路径） ────────────────────────────────────
@pytest.mark.skipif(not _VERIFY.is_file(), reason="需要验证脚本")
def test_runs_without_pythonpath() -> None:
    """CI 是直接执行脚本的，此时仓库根不在 sys.path 上。"""
    env = {**os.environ}
    env.pop("PYTHONPATH", None)
    env["PYTHONUTF8"] = "1"
    result = subprocess.run(
        [sys.executable, str(_VERIFY)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=60,
        check=False,
    )
    combined = (result.stdout or "") + (result.stderr or "")
    assert "ModuleNotFoundError" not in combined, combined[-600:]
    assert result.returncode == 2, f"缺参数应返回 2，实际 {result.returncode}"


@pytest.mark.skipif(not _VERIFY.is_file(), reason="需要验证脚本")
def test_sibling_imports_are_path_based() -> None:
    """兄弟模块必须按文件路径加载，不能靠 `from tools.xxx`。"""
    import re

    source = _VERIFY.read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    offenders = re.findall(r"^\s*(?:from tools[.\s]|import tools\b)", code, re.MULTILINE)
    assert not offenders, f"发现依赖 sys.path 含仓库根的导入：{offenders}"


# ── 旧流程必须已下线 ─────────────────────────────────────────────────────
def _workflow_text() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in sorted(_WORKFLOWS.glob("*.yml"))
    )


def test_equivalence_step_is_gone() -> None:
    """旧的「能力等价性对账」步骤不得再出现在任何 workflow 里。

    它会拿两份静态 JSON 比数字，报出的缺口多是 `full` 档能力，
    对本就按需装组件的 Standard 是**错误判据**。
    """
    text = _workflow_text()
    assert "compare-capabilities" not in text, "等价比对步骤又回来了"
    assert "等价性对账" not in text, "等价性对账的注释块又回来了"


def test_full_portable_build_is_gone() -> None:
    """不得再构建 Full 大号冻结包。

    判据是「没有 `--edition Full` / `-Edition Full`」，因为那正是
    每平台多跑一轮 PyInstaller 的入口。
    """
    text = _workflow_text()
    for needle in ("--edition Full", "-Edition Full"):
        assert needle not in text, f"workflow 里又出现了 {needle!r}"


def test_component_verification_step_is_wired() -> None:
    """新的验证步骤必须真的被 CI 执行（漏接线 = 承诺了不存在的保障）。"""
    text = _workflow_text()
    assert "verify_standard_components.py" in text, "组件验证步骤没有接线"
