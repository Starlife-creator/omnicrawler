"""P0（优化方案 §14.2）：check_network_boundaries 扩词表回归 + 反向断言。

- 正向：新词表能命中 build_opener / ProxyHandler 直连（含 from-import 别名形态）；
- 反向断言（AGENTS.md 判据纪律）：临时树装回 fail-open 缺口 ⇒ 门禁必须红；
  白名单内文件 ⇒ 门禁绿；选中 0 个文件时不误报。
"""
from __future__ import annotations

from pathlib import Path

import tools.check_network_boundaries as gate


def _write(root: Path, rel: str, body: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_vocab_covers_build_opener_and_proxy_handler() -> None:
    assert "urllib.request.build_opener" in gate.DIRECT_TRANSPORTS
    assert "urllib.request.ProxyHandler" in gate.DIRECT_TRANSPORTS


def test_finds_dotted_and_aliased_build_opener(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "pkg/leak.py",
        "import urllib.request\n"
        "from urllib.request import build_opener as bo, ProxyHandler\n"
        "\n"
        "def a():\n"
        "    return urllib.request.build_opener()\n"
        "\n"
        "def b():\n"
        "    return bo()\n"
        "\n"
        "def c():\n"
        "    return ProxyHandler({})\n",
    )
    findings = gate.find_direct_calls(tmp_path / "pkg")
    transports = {item[2] for item in findings}
    assert transports == {"urllib.request.build_opener", "urllib.request.ProxyHandler"}
    assert len(findings) == 3


def test_reverse_assertion_gap_makes_gate_red(tmp_path: Path, capsys) -> None:
    """装回 P0 缺口（未白名单文件直连 build_opener）⇒ main 必须返回 1。"""
    _write(tmp_path, "pkg/doctor_like.py", "import urllib.request\nopener = urllib.request.build_opener()\n")
    assert gate.main(["--source-root", str(tmp_path / "pkg")]) == 1
    err = capsys.readouterr()
    assert "unclassified" in err.out
    assert "failed" in err.err


def test_clean_tree_passes_and_zero_files_is_green(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/clean.py", "x = 1\n")
    assert gate.main(["--source-root", str(tmp_path / "pkg")]) == 0
    empty = tmp_path / "empty"
    empty.mkdir()
    # 空树没有可扫文件：不得因「选中 0 个」而假红，也不得崩溃。
    assert gate.main(["--source-root", str(empty)]) == 0


def test_whitelist_entries_exist_in_real_source() -> None:
    """白名单不是死条目：每个登记的相对路径必须真实存在于 src/omnicrawler。"""
    root = Path(__file__).resolve().parents[3] / "src" / "omnicrawler"
    missing = [rel for rel in gate.APPROVED_DIRECT_CALLS if not (root / rel).is_file()]
    assert missing == []


def test_real_source_has_no_unapproved_direct_calls() -> None:
    root = Path(__file__).resolve().parents[3] / "src" / "omnicrawler"
    findings = gate.find_direct_calls(root)
    unexpected = [item for item in findings if item[0] not in gate.APPROVED_DIRECT_CALLS]
    assert unexpected == [], f"未分类直连: {unexpected}"
