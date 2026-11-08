"""批次 4（优化方案 §14.5 选项①）：锚段摘要门禁的判据。

★ 这个门禁守的是「全项目唯一不可擅动、却唯一零守卫的真源」——项目根 `优化方案.md`
的 §一 / §1.1 / §1.2 / §1.3。它所在的根目录**不是 git 仓**，所以改锚不会有任何 diff；
本门禁把「静默改锚」变成「必须显式改摘要 + 提交一次 diff」。

用例重点不是「摘要算得对」，而是**缺口能不能被发现**：
锚段被改 / 被删 / 标题被改名 / 登记表为空 / 锚文件被改名，每一种都必须红。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import tools.check_anchor_integrity as gate

_HEAD = "# OmniCrawler 优化方案（现行版）\n\n> 适用版本：**1.0.1**\n\n"

_BODY = (
    "## 一、最终目标\n"
    "\n"
    "将 OmniCrawler 建设为世界一流水平的数据采集平台。\n"
    "\n"
    "### 1.1 不变的现实条件\n"
    "\n"
    "- 开发阶段，没有用户和社区开发者。\n"
    "\n"
    "### 1.2 最终应达到的产品效果\n"
    "\n"
    "| 方向 | 说明 |\n"
    "| --- | --- |\n"
    "| 安全与保护 | 凭据与数据得到保护。 |\n"
    "\n"
    "### 1.3 判据层级与原则治理（2026-09-11 定案）\n"
    "\n"
    "**判据分两层，锚必须先于原则：**\n"
    "\n"
    "## 二、分阶段目标\n"
    "\n"
    "不属于锚。\n"
)


def _stage(tmp_path: Path, body: str = _BODY) -> Path:
    """搭一棵最小工作区：`<tmp>/OmniCrawler`（主仓）+ `<tmp>/优化方案.md`（根级锚文件）。"""
    repo = tmp_path / "OmniCrawler"
    repo.mkdir()
    (tmp_path / "优化方案.md").write_text(_HEAD + body, encoding="utf-8")
    return repo


def _digest_of(body: str = _BODY) -> dict[str, str]:
    return gate.compute_digest(_HEAD + body)


# --- 正向：切段与摘要 -------------------------------------------------------


def test_extracts_all_declared_sections() -> None:
    sections = gate.extract_sections(_HEAD + _BODY)
    assert set(sections) == {section_id for section_id, _ in gate.ANCHOR_SECTIONS}
    assert "建设为世界一流水平" in sections["§一 最终目标"]


def test_section_boundaries_stop_at_next_heading() -> None:
    """§1.1 必须止于 §1.2 之前，不能把后面整段吞进来。"""
    sections = gate.extract_sections(_HEAD + _BODY)
    assert "没有用户和社区开发者" in sections["§1.1 不变的现实条件"]
    assert "最终应达到的产品效果" not in sections["§1.1 不变的现实条件"]


def test_last_section_stops_before_next_level_two_heading() -> None:
    """§1.3 是最后一段：必须止于 `## 二、`，不能吞掉非锚正文。"""
    sections = gate.extract_sections(_HEAD + _BODY)
    assert "锚必须先于原则" in sections["§1.3 判据层级与原则治理"]
    assert "不属于锚" not in sections["§1.3 判据层级与原则治理"]


def test_line_ending_and_trailing_whitespace_are_not_anchor_changes(tmp_path: Path) -> None:
    """CRLF 与行尾空白不算改锚——否则门禁会被无关改动打红，最终被人关掉。"""
    crlf = (_HEAD + _BODY).replace("\n", "\r\n")
    digest = _digest_of()
    assert gate.compute_digest(crlf) == digest


def test_trailing_separator_is_not_an_anchor_change() -> None:
    body = _BODY.replace("## 二、分阶段目标", "---\n\n## 二、分阶段目标")
    assert gate.compute_digest(_HEAD + body) == _digest_of()


# --- 反向断言：每一种缺口都必须红 -------------------------------------------


def test_clean_workspace_passes(tmp_path: Path) -> None:
    repo = _stage(tmp_path)
    assert gate.check(repo, _digest_of()) == []


def test_reverse_assertion_edited_anchor_is_red(tmp_path: Path) -> None:
    """改一个字（不是版式）⇒ 必须红，并指名是哪一段。"""
    repo = _stage(tmp_path, _BODY.replace("世界一流水平", "世界第一水平"))
    problems = gate.check(repo, _digest_of())
    assert len(problems) == 1
    assert "anchor section changed" in problems[0]
    assert "§一 最终目标" in problems[0]


def test_reverse_assertion_deleted_section_is_red(tmp_path: Path) -> None:
    repo = _stage(tmp_path, _BODY.replace("### 1.1 不变的现实条件", "### 1.1 现状"))
    problems = gate.check(repo, _digest_of())
    assert any("anchor section not found" in problem for problem in problems)
    assert any("§1.1 不变的现实条件" in problem for problem in problems)


def test_reverse_assertion_empty_digest_is_red(tmp_path: Path) -> None:
    """「选中 0 段」不是通过——登记表空了说明判据已失效。"""
    repo = _stage(tmp_path)
    problems = gate.check(repo, {})
    assert any("empty selection is not a pass" in problem for problem in problems)


def test_reverse_assertion_unknown_section_id_is_red(tmp_path: Path) -> None:
    repo = _stage(tmp_path)
    digest = _digest_of()
    digest["§9.9 不存在的段"] = "0" * 64
    problems = gate.check(repo, digest)
    assert any("unknown section id" in problem for problem in problems)


def test_reverse_assertion_unregistered_section_is_red(tmp_path: Path) -> None:
    """代码声明了 4 段、登记表只有 3 段 ⇒ 红（少守一段不能静默）。"""
    repo = _stage(tmp_path)
    digest = _digest_of()
    digest.pop("§1.2 最终应达到的产品效果")
    problems = gate.check(repo, digest)
    assert any("declared in code but not registered" in problem for problem in problems)


def test_reverse_assertion_renamed_anchor_file_is_red(tmp_path: Path) -> None:
    """锚文件被改名 ⇒ 不能静默 SKIP，必须报「疑似改名」。"""
    repo = _stage(tmp_path)
    (tmp_path / "优化方案.md").rename(tmp_path / "优化方案_v2.md")
    problems = gate.check(repo, _digest_of())
    assert any("look like a renamed anchor file" in problem for problem in problems)


def test_missing_anchor_file_is_a_skip_not_a_failure(tmp_path: Path) -> None:
    """CI 拿不到根目录：没有锚文件、也没有改名嫌疑时，显式 SKIP（返回 0）。"""
    repo = tmp_path / "OmniCrawler"
    repo.mkdir()
    assert gate.main(["--root", str(repo)]) == 0


# --- 端到端：main 的三种出口 -------------------------------------------------


def test_main_passes_on_real_repository() -> None:
    """真实仓库：摘要文件已登记且锚未变 ⇒ 退出 0。"""
    assert gate.main([]) == 0


def test_main_reports_changed_anchor_via_json(tmp_path: Path, capsys) -> None:
    repo = _stage(tmp_path, _BODY.replace("世界一流水平", "世界第一水平"))
    code = gate.main(["--root", str(repo), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["ok"] is False
    assert any("anchor section changed" in problem for problem in payload["problems"])


def test_main_update_writes_digest(tmp_path: Path, monkeypatch) -> None:
    repo = _stage(tmp_path, _BODY.replace("世界一流水平", "世界第一水平"))
    target = tmp_path / "anchor_digest.json"
    monkeypatch.setattr(gate, "DIGEST_PATH", target)
    assert gate.main(["--root", str(repo), "--update"]) == 0
    written = json.loads(target.read_text(encoding="utf-8"))
    assert set(written["sections"]) == {section_id for section_id, _ in gate.ANCHOR_SECTIONS}
    # 更新后同一棵树必须变绿（否则 --update 等于没修）
    assert gate.check(repo, gate.load_digest(target)) == []


def test_main_update_refuses_when_anchor_section_is_missing(tmp_path: Path, monkeypatch) -> None:
    """锚段缺失时 --update 必须拒绝——否则「删掉一段再重算摘要」就能洗白。"""
    repo = _stage(tmp_path, _BODY.replace("### 1.1 不变的现实条件", "### 1.1 现状"))
    monkeypatch.setattr(gate, "DIGEST_PATH", tmp_path / "anchor_digest.json")
    assert gate.main(["--root", str(repo), "--update"]) == 1


def test_registered_digest_matches_the_real_anchor_file() -> None:
    """登记值不是死数据：必须真的对应根级锚文件当前内容。"""
    real = gate.anchor_file(gate.REPO_ROOT)
    if not real.is_file():
        # CI checkout 只有主仓、拿不到项目根：明确 skip，不伪装成「验证通过」。
        pytest.skip("anchor file is outside the CI checkout (root workspace is local-only)")
    expected = gate.load_digest()
    actual = gate.compute_digest(real.read_text(encoding="utf-8"))
    assert actual == expected


def test_digest_file_has_no_manual_edits() -> None:
    """摘要文件是生成物：结构固定、值是 64 位十六进制、无未知段。"""
    payload = json.loads(gate.DIGEST_PATH.read_text(encoding="utf-8"))
    assert set(payload) == {"_comment", "_source", "sections"}
    assert payload["_source"] == "../优化方案.md"
    for section_id, value in payload["sections"].items():
        assert section_id in {sid for sid, _ in gate.ANCHOR_SECTIONS}
        assert len(value) == 64
        assert all(char in "0123456789abcdef" for char in value), f"{section_id}: 非 sha256 十六进制"
