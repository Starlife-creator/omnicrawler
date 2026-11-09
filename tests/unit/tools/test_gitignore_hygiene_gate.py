"""公开 `.gitignore` 的注释不得泄露本机布局——判据要能被反向断言证明。

背景（2026-11-09）：为清掉常年挂在 `git status` 上的本地产物目录，`.gitignore`
新增了七条规则。规则本身没问题，**注释**有问题：注释里写下了本机用户名、
活动工作树、某个测试把产物落到仓库根的细节、以及「验收证据库在仓库外
（见某份不在仓内的方案文档）」这类悬空引用。仓库是 public，注释与规则一起公开。

不能靠删规则或忽略 `.gitignore` 解决（后者会让 `.env`/`*.pem` 等防泄密规则
不随仓分发，是安全回退）。所以立这条门禁：规则照留，注释只写通用形态；
机器专属项放 `.git/info/exclude`（不进版本库）。

本文件断言三件事：
1. 三类泄露形态各有一个正向用例（真的会红）；
2. 每条判据都有**反向断言**——把缺口装回去必须变红，否则判据可能是恒真的；
3. 现行仓库通过（这就是它要守的真实对象）。
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

import pytest

from tools import check_gitignore_hygiene as hygiene

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GITIGNORE = _REPO_ROOT / ".gitignore"


def _rules(findings: list[hygiene.Finding]) -> set[str]:
    return {finding.rule for finding in findings}


def _write(tmp_path: Path, text: str) -> Path:
    target = tmp_path / ".gitignore"
    target.write_text(text, encoding="utf-8")
    return target


def _check_with_tracking_ok(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[hygiene.Finding]:
    """只测注释判据：把「是否被 git 跟踪」这一项短路成通过，避免依赖临时仓。"""
    monkeypatch.setattr(hygiene, "_check_tracked", lambda root: [])
    return hygiene.check(tmp_path)


def test_repository_gitignore_passes() -> None:
    """现行 `.gitignore` 必须通过——这是门禁真正要守的对象。"""
    findings = hygiene.check(_REPO_ROOT)
    assert not findings, "现存 .gitignore 未通过：\n" + "\n".join(f.describe() for f in findings)


def test_repository_gitignore_keeps_the_ignore_rules(tmp_path: Path) -> None:
    """中性化只动注释：六条本地产物规则与 pytest 兜底规则必须仍在。"""
    text = _GITIGNORE.read_text(encoding="utf-8")
    lines = {line.strip() for line in text.splitlines()}
    for rule in (
        "pytest-of-*/",
        ".agents/",
        ".scratch/",
        "imported-work/",
        "user-test-runs/",
        ".field_designer/",
        ".field_designer.yaml",
    ):
        assert rule in lines, f"忽略规则被误删: {rule}"


def test_absolute_path_in_comment_is_red(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write(tmp_path, "# 产物落在 E:\\tool\\somewhere\\repo 下\nbuild/\n")
    findings = _check_with_tracking_ok(monkeypatch, tmp_path)
    assert "absolute-path" in _rules(findings), [f.describe() for f in findings]


def test_posix_absolute_path_in_comment_is_red(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write(tmp_path, "# 见 /home/someone/projects/repo\nbuild/\n")
    findings = _check_with_tracking_ok(monkeypatch, tmp_path)
    assert "absolute-path" in _rules(findings)


def test_url_is_not_mistaken_for_a_windows_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`https://...` 里的 `s://` 不能被误判成盘符路径——否则判据会天天假红。"""
    _write(tmp_path, "# 规则说明见 https://example.com/docs/ignore\nbuild/\n")
    findings = _check_with_tracking_ok(monkeypatch, tmp_path)
    assert "absolute-path" not in _rules(findings), [f.describe() for f in findings]


def test_local_user_name_in_comment_is_red(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """本机用户名必须由 `getpass.getuser()` 取，不写死在门禁里。"""
    name = getpass.getuser()
    if not name:
        pytest.skip("本机取不到用户名，无法构造该用例")
    _write(tmp_path, f"# pytest 兜底目录 pytest-of-{name}/\npytest-of-*/\n")
    findings = _check_with_tracking_ok(monkeypatch, tmp_path)
    assert "local-user-name" in _rules(findings), [f.describe() for f in findings]


def test_user_name_match_is_case_insensitive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    name = getpass.getuser()
    if not name:
        pytest.skip("本机取不到用户名，无法构造该用例")
    _write(tmp_path, f"# 目录名里带着 {name.upper()}\npytest-of-*/\n")
    findings = _check_with_tracking_ok(monkeypatch, tmp_path)
    assert "local-user-name" in _rules(findings)


def test_reverse_assertion_clean_comment_is_not_flagged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """反向断言：中性注释不得被判红，否则上面的正向用例可能只是「什么都红」。"""
    _write(tmp_path, "# 目录名带运行者的系统用户名（各平台不同），故按前缀通配\npytest-of-*/\n")
    findings = _check_with_tracking_ok(monkeypatch, tmp_path)
    assert not findings, [f.describe() for f in findings]


def test_dangling_markdown_reference_is_red(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write(tmp_path, "# 详细口径见 优化方案.md §14.8\nbuild/\n")
    findings = _check_with_tracking_ok(monkeypatch, tmp_path)
    assert "dangling-doc-reference" in _rules(findings), [f.describe() for f in findings]


def test_existing_markdown_reference_is_allowed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """仓内确实存在的 `.md` 引用是合法写法（如 `artifacts/README.md`）。"""
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "README.md").write_text("# x\n", encoding="utf-8")
    _write(tmp_path, "# 说明见 artifacts/README.md\nartifacts/\n")
    findings = _check_with_tracking_ok(monkeypatch, tmp_path)
    assert not findings, [f.describe() for f in findings]


def test_section_pointer_without_a_document_is_red(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`§14.8` 这类指向只存在于某人工作区的章节号，对别人是不可跟随的指针。"""
    _write(tmp_path, "# 口径见 §14.8\nbuild/\n")
    findings = _check_with_tracking_ok(monkeypatch, tmp_path)
    assert "unfollowable-section-pointer" in _rules(findings), [f.describe() for f in findings]


def test_section_pointer_with_an_in_repo_document_is_allowed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """反向断言：带着仓内文档名的章节指针不该被判红（否则判据过宽）。"""
    (tmp_path / "CONTRIBUTING.md").write_text("# x\n", encoding="utf-8")
    _write(tmp_path, "# 口径见 CONTRIBUTING.md §3\nbuild/\n")
    findings = _check_with_tracking_ok(monkeypatch, tmp_path)
    assert not findings, [f.describe() for f in findings]


def test_empty_selection_is_not_a_pass(tmp_path: Path) -> None:
    """没有一行注释时不得判通过——空集合是「没检查」，不是「没问题」。"""
    _write(tmp_path, "build/\ndist/\n")
    findings = hygiene.check(tmp_path)
    assert "empty-selection" in _rules(findings), [f.describe() for f in findings]


def test_missing_gitignore_is_red(tmp_path: Path) -> None:
    findings = hygiene.check(tmp_path)
    assert "missing" in _rules(findings)


def test_untracked_gitignore_is_red(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`.gitignore` 不被跟踪 ⇒ 新 clone 拿不到它，防泄密规则整批失效。"""
    _write(tmp_path, "# 中性注释\nbuild/\n")
    monkeypatch.setattr(hygiene, "_git", lambda root, *args: (1, ""))
    findings = hygiene.check(tmp_path)
    assert "not-tracked" in _rules(findings), [f.describe() for f in findings]


def test_self_ignored_gitignore_is_red(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """把 `.gitignore` 自己写进忽略规则 ⇒ 必须判红（这正是有人会提的「解法」）。"""
    _write(tmp_path, "# 中性注释\n.gitignore\nbuild/\n")

    def fake_git(root: Path, *args: str) -> tuple[int, str]:
        if args[:2] == ("ls-files", "--error-unmatch"):
            return 0, ".gitignore\n"
        if args[:1] == ("check-ignore",):
            return 0, ".gitignore\n"
        return 1, ""

    monkeypatch.setattr(hygiene, "_git", fake_git)
    findings = hygiene.check(tmp_path)
    assert "self-ignored" in _rules(findings), [f.describe() for f in findings]


def test_reverse_assertion_old_blob_would_have_been_caught() -> None:
    """反向断言：中性化之前的注释（含用户名）必须被这套判据抓到。

    用提交历史上的真实文本片段构造，证明门禁不是「写成什么样都过」。
    """
    name = getpass.getuser()
    if not name:
        pytest.skip("本机取不到用户名，无法构造该用例")
    old_comments = [
        (56, "目录名带本机用户名（Linux/CI 上各不相同），故按前缀通配，不写死 " + name + "。"),
        (145, "真正的验收证据库在仓库外（见优化方案 §14.8）"),
    ]
    hits: list[str] = []
    for line, detail in old_comments:
        hits.extend(f.rule for f in hygiene._check_comment(line, detail, user_names=[name], root=_REPO_ROOT))
    assert "local-user-name" in hits
    assert "dangling-doc-reference" in hits or "unfollowable-section-pointer" in hits


def test_json_output_is_ascii_and_parseable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys) -> None:
    """`--json` 输出必须是纯 ASCII 且可解析（门禁要能在 cp1252 控制台上跑）。"""
    import json

    _write(tmp_path, "# 中性注释\nbuild/\n")
    monkeypatch.setattr(hygiene, "_check_tracked", lambda root: [])
    code = hygiene.main(["--root", str(tmp_path), "--json"])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert code == 0
    assert payload["ok"] is True
    assert out.isascii(), "JSON 输出含非 ASCII 字符，会在非 UTF-8 控制台上崩"


def test_main_prints_ascii_only_on_success(capsys) -> None:
    code = hygiene.main(["--root", str(_REPO_ROOT)])
    out = capsys.readouterr().out
    assert code == 0
    assert out.isascii()


def test_gate_is_registered_in_static_set() -> None:
    """门禁必须进 `static` 集合，否则 `self_verify --set static` 不会跑到它。"""
    from tools.gate_registry import gates_for

    names = {gate.name for gate in gates_for(("static",))}
    assert "check_gitignore_hygiene" in names


def test_repo_gitignore_is_actually_checked_by_git() -> None:
    """确认 `git check-ignore` 对 `.gitignore` 返回 1（未被任何规则匹配）。"""
    code, output = hygiene._git(_REPO_ROOT, "check-ignore", "-q", "--no-index", ".gitignore")
    assert code == 1, f".gitignore 被某条规则匹配了（rc={code}）: {output}"


def test_module_reachable_from_the_repo_root() -> None:
    """`tools/` 需可 import——门禁测试与 CI 都靠这一点。"""
    assert hygiene.REPO_ROOT == _REPO_ROOT
    assert Path(hygiene.__file__).resolve() == (_REPO_ROOT / "tools" / "check_gitignore_hygiene.py").resolve()
    assert sys.version_info >= (3, 12)
