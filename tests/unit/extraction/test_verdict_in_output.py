"""自动分析产出的配置必须**自带结论**，而不只是往 stderr 打一行。

## 事故形态（2026-11-27 实测）

docs.python.org 的代码块页面：DOM 里 7 个 `<pre>`，字段名全是兜底名
`内容_span` / `内容_span_2` / …（认不出语义）。

    auto-analyze <url> -o cfg.yaml      # exit 0，文件写出来了

stderr 上有一行「结论: 未找到业务列表」，**但 stdout / 文件里那份 YAML 看起来完全正常**
（有 `source` / `crawl` / `extract` / `outputs`）。用户 `-o` 之后就把 stderr 丢了，
带走一份「取到的全是兜底列名」的配置，跑起来才发现每列都叫 `内容_span`。

## 判据：结论必须跟着**文件**走

把结论写成 YAML 头部注释。注释不改变语义（`yaml.safe_load` 后 key 集合完全相同），
但会跟着文件一起被带走、被分享、被 `git diff` 看到。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import omnicrawler.extraction.intelligent_scraper as scraper_module
from omnicrawler.extraction.intelligent_scraper import main


_PAGE = """
<html><body>
  <div class="list">
    <article class="card"><h3>甲</h3><span class="price">1</span></article>
    <article class="card"><h3>乙</h3><span class="price">2</span></article>
    <article class="card"><h3>丙</h3><span class="price">3</span></article>
  </div>
</body></html>
"""


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> str:
    page = tmp_path / "page.html"
    page.write_text(_PAGE, encoding="utf-8")
    out = tmp_path / "cfg.yaml"
    # 必须给真实 URL：自动配置拒绝占位地址（`file:///placeholder`）。
    monkeypatch.setattr(
        "sys.argv",
        ["scraper", str(page), "--url", "https://example.com/list", "-o", str(out)],
    )
    try:
        main(task_settings=None)
    except SystemExit as exc:
        assert exc.code == 0, capsys.readouterr().err
    return out.read_text(encoding="utf-8")


def test_output_starts_with_verdict_comment(tmp_path: Path, monkeypatch, capsys) -> None:
    """★ 文件第一行就是结论 —— 用户丢不掉的只有文件。"""
    text = _run(tmp_path, monkeypatch, capsys)
    first = text.splitlines()[0]
    assert first.startswith("# 自动分析结论: "), first


def test_comment_does_not_change_yaml_semantics(tmp_path: Path, monkeypatch, capsys) -> None:
    """注释不能改变解析结果：`safe_load` 出来的 key 集合必须与无注释时相同。"""
    text = _run(tmp_path, monkeypatch, capsys)
    parsed = yaml.safe_load(text)
    assert isinstance(parsed, dict)
    # 一个真实的配置至少有这几节；少了哪节说明注释把内容吃掉了
    for key in ("project", "source", "extract"):
        assert key in parsed, f"{key} 丢了：{sorted(parsed)}"


def test_verdict_comment_matches_stderr_verdict(tmp_path: Path, monkeypatch, capsys) -> None:
    """文件里的结论与 stderr 上那行必须**同源**（不能各说一套）。"""
    text = _run(tmp_path, monkeypatch, capsys)
    err = capsys.readouterr().err
    header = text.splitlines()[0]
    # 文件头是 `# 自动分析结论: <verdict>[ —— <why>]`；stderr 是 `结论: <verdict>[ —— <why>]`。
    # 判据取**结论主体**（去掉两边各自的前缀）后比较，钉住"同源"而不是钉住措辞。
    file_verdict = header.removeprefix("# 自动分析结论: ")
    err_verdict = next(
        line.removeprefix("结论: ") for line in err.splitlines() if line.startswith("结论: ")
    )
    assert file_verdict == err_verdict, (file_verdict, err_verdict)
