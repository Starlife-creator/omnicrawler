"""`tools/check_subprocess_encoding.py` 的反向断言与正向用例。

重点不是「它绿了」，而是**它会不会装死**：扫描面收窄、白名单变宽、判据认错调用，
三种失效都要求能被钉住。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools import check_subprocess_encoding as gate

_REPO_ROOT = Path(__file__).resolve().parents[3]


# ---------- 扫描面与判据本身 ----------


def test_scan_is_not_vacuous() -> None:
    """扫描面必须非空，且覆盖到已知的三类位点（tools / tests / src）。"""
    sites, findings = gate.scan(_REPO_ROOT)
    keys = {site.key for site in sites}

    assert len(sites) >= 20, f"只扫到 {len(sites)} 处，扫描面明显不对"
    assert any(key.startswith("tools/") for key in keys), "没扫到 tools/"
    assert any(key.startswith("tests/") for key in keys), "没扫到 tests/"
    assert any(key.startswith("src/") for key in keys), "没扫到 src/"
    assert findings == [], findings


def test_read_text_encoding_is_not_mistaken_for_subprocess(tmp_path: Path) -> None:
    """**反向断言**：`path.read_text(encoding=...)` 不是子进程调用，不得被判违规。"""
    target = tmp_path / "sample.py"
    target.write_text(
        'from pathlib import Path\n\nPath("a.txt").read_text(encoding="utf-8")\n',
        encoding="utf-8",
    )
    assert gate.scan_file(tmp_path, "sample.py") == []


def test_subprocess_without_encoding_is_ignored(tmp_path: Path) -> None:
    """不带 `encoding=` 的调用不在判据内（那种调用方拿的是 bytes，与本文缺陷无关）。"""
    target = tmp_path / "sample.py"
    target.write_text(
        "import subprocess\n\nsubprocess.run(['git', 'status'], capture_output=True)\n",
        encoding="utf-8",
    )
    assert gate.scan_file(tmp_path, "sample.py") == []


def test_subprocess_with_encoding_and_env_is_clean(tmp_path: Path) -> None:
    """带上 `env=` 就是合规写法 —— 这是门禁推荐的修法，不该判红。"""
    target = tmp_path / "sample.py"
    target.write_text(
        "import subprocess\n\n"
        "subprocess.run(['x'], encoding='utf-8', env={'A': '1'})\n",
        encoding="utf-8",
    )
    sites = gate.scan_file(tmp_path, "sample.py")
    assert len(sites) == 1
    assert sites[0].has_env is True


def test_subprocess_with_encoding_without_env_is_flagged(tmp_path: Path) -> None:
    """**反向断言（核心）**：新塞一个违规位点 ⇒ 必红，且理由里点名该位点。"""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "bad.py").write_text(
        "import subprocess\n\n"
        "def go():\n"
        "    return subprocess.run(['x'], capture_output=True, text=True, encoding='utf-8')\n",
        encoding="utf-8",
    )
    (tmp_path / ".git").mkdir()

    import subprocess as real_subprocess

    class _Done:
        returncode = 0
        stdout = "pkg/bad.py"
        stderr = ""

    original_run = real_subprocess.run

    def fake_run(*args, **kwargs):
        return _Done()

    gate.subprocess.run = fake_run  # type: ignore[assignment]
    try:
        sites, findings = gate.scan(tmp_path)
    finally:
        gate.subprocess.run = original_run  # type: ignore[assignment]

    assert len(sites) == 1
    rules = {finding.rule for finding in findings}
    assert "unsafe-subprocess-encoding" in rules, findings
    joined = " ".join(finding.detail for finding in findings)
    assert "pkg/bad.py:4" in joined
    assert "utf8_env" in joined, "判据要告诉人怎么修，而不只是「不许」"


# ---------- 白名单的三个失效形态 ----------


def test_allowance_without_reason_is_red(monkeypatch: pytest.MonkeyPatch) -> None:
    """白名单写理由不是装饰：空理由必须判红，否则豁免就成了后门。"""
    monkeypatch.setattr(gate, "ALLOWED", {"src/omnicrawler/services/replay.py:227": "   "})
    _, findings = gate.scan(_REPO_ROOT)
    rules = {finding.rule for finding in findings}
    assert "blank-justification" in rules, findings


def test_stale_allowance_is_red(monkeypatch: pytest.MonkeyPatch) -> None:
    """**反向断言**：豁免指向一个已不存在的位点 ⇒ 必红。

    这条防的是「改好了但白名单还留着」——每留一条，守卫能覆盖的面就悄悄小一寸。
    """
    monkeypatch.setattr(
        gate,
        "ALLOWED",
        {"tests/unit/tools/test_never_existed.py:999": "历史遗留"},
    )
    _, findings = gate.scan(_REPO_ROOT)
    rules = {finding.rule for finding in findings}
    assert "stale-allowance" in rules, findings


def test_every_allowance_actually_exists_in_the_repository() -> None:
    """真仓的白名单每一条都必须真实命中一个位点（否则上面那条只是空转）。"""
    sites, _ = gate.scan(_REPO_ROOT)
    keys = {site.key for site in sites}
    missing = sorted(key for key in gate.ALLOWED if key not in keys)
    assert missing == [], f"以下豁免已失效，请删除：{missing}"


def test_every_allowance_has_a_reason() -> None:
    blank = sorted(key for key, reason in gate.ALLOWED.items() if not reason.strip())
    assert blank == [], f"以下豁免没有写理由：{blank}"


# ---------- 空集不是通过 ----------


def test_no_python_files_is_red(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """扫描面为 0 时必须判红 —— 空集不是通过。"""
    monkeypatch.setattr(gate, "tracked_python_files", lambda root: [])
    _, findings = gate.scan(tmp_path)
    assert any(finding.rule == "empty-selection" for finding in findings), findings


def test_no_spawn_sites_is_red(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """文件不少但一个位点都没命中 ⇒ 判据本身失效，同样判红。"""
    monkeypatch.setattr(gate, "tracked_python_files", lambda root: ["a.py"])
    monkeypatch.setattr(gate, "scan_file", lambda root, rel: [])
    _, findings = gate.scan(tmp_path)
    assert any(finding.rule == "empty-selection" for finding in findings), findings


# ---------- 接线与 CLI ----------


def test_gate_is_registered_and_wired() -> None:
    """新门禁必须同时出现在清单、static 集合与 CI 工作流里（否则本地绿、CI 不跑）。"""
    from tools.gate_registry import _STATIC, GATES

    names = {gate_.name for gate_ in GATES}
    assert "check_subprocess_encoding" in names, "未登记进 tools/gate_registry.py"

    entry = next(gate_ for gate_ in GATES if gate_.name == "check_subprocess_encoding")
    assert entry.sets == _STATIC, "应属于 static 集合（秒级、无前置产物）"
    assert entry.args == ("tools/check_subprocess_encoding.py",)

    workflow = (_REPO_ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
    assert "python tools/check_subprocess_encoding.py" in workflow, "CI 未接线"


def test_main_reports_failure_with_nonzero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI 形态：有问题返回 1 并把问题写到 stderr。"""
    monkeypatch.setattr(gate, "tracked_python_files", lambda root: [])
    code = gate.main(["--root", str(tmp_path)])
    assert code == 1
    assert "子进程编码约定检查失败" in capsys.readouterr().err
