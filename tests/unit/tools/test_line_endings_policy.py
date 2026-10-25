"""`tools/check_line_endings_policy.py` 行尾规则一致性门禁反向断言（C-#23）。

护栏：``.gitattributes`` 现行规则缺一条必须报红；``AGENTS.md`` 残留「主仓文件
是 CRLF」过期表述必须报红；规则文档缺准据引用必须报红；真实仓库必须全绿。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]


def _load():
    spec = importlib.util.spec_from_file_location(
        "_line_endings_policy_under_test", _ROOT / "tools" / "check_line_endings_policy.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


POLICY = _load()

_GOOD_ATTRIBUTES = """* text=auto
*.bat   text eol=crlf
*.cmd   text eol=crlf
*.ps1   text eol=lf
*.sh    text eol=lf
*.command text eol=lf
"""


def _write_root(
    tmp_path: Path,
    attributes: str = _GOOD_ATTRIBUTES,
    agents: str = "行尾规则以 `.gitattributes` 为准据。\n",
) -> None:
    (tmp_path / ".gitattributes").write_text(attributes, encoding="utf-8")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "CODING_STANDARDS.md").write_text("规则见 `.gitattributes`。\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text(agents, encoding="utf-8")


def test_real_repo_policy_passes() -> None:
    assert POLICY.check(_ROOT) == []


def test_missing_gitattributes_reports_single_issue(tmp_path: Path) -> None:
    issues = POLICY.check(tmp_path)
    assert issues == [".gitattributes 缺失，无法校验行尾规则"]


def test_missing_rule_reported(tmp_path: Path) -> None:
    _write_root(tmp_path, attributes="* text=auto\n")

    issues = POLICY.check(tmp_path)
    message = "\n".join(issues)
    assert "*.bat" in message
    assert "*.ps1" in message
    assert "*.sh" in message
    assert "*.command" in message


def test_stale_crlf_claim_in_agents_reported(tmp_path: Path) -> None:
    _write_root(
        tmp_path,
        agents="行尾按字节实测（本仓主仓文件是 CRLF，市场仓是 LF）。\n",
    )

    issues = POLICY.check(tmp_path)
    assert any("本仓主仓文件是 CRLF" in issue for issue in issues)
    # 准据引用缺失也要报。
    assert any(".gitattributes" in issue and "准据" in issue for issue in issues)


def test_stale_claim_in_coding_standards_reported(tmp_path: Path) -> None:
    _write_root(tmp_path)
    (tmp_path / "docs" / "CODING_STANDARDS.md").write_text("本仓全部使用 CRLF。\n", encoding="utf-8")

    issues = POLICY.check(tmp_path)
    assert any("全部使用 CRLF" in issue for issue in issues)
