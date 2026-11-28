"""CSS 选择器语法错误必须在**配置校验**阶段就报（2026-11-28）。

## 缺陷

-- 见 docstring --

`selector: div[[[[broken` 此前能通过 `omnicrawler validate`，直到真正跑采集才抛
`SelectorSyntaxError` —— 报错位置在解析器里，看不出是「配置写错了」。

## 判据复用引擎自己的解析器

`html_tools.selector_is_valid()` 走的就是 `select_nodes` 那条**同一条**
`_compiled_selector` 路径（soupsieve 编译，带缓存）。不自己写正则猜语法 ——
那会造出「校验说行、运行说不行」的第二套判据。

`core/config.py` 不能顶层 import `extraction.html_tools`（会拉起 bs4），
所以这条校验在 `_validate_css_field_selectors` 里**函数内导入**。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]


def _write_config(tmp_path: Path, selector: str, *, mode: str = "html") -> Path:
    config = tmp_path / "c.yaml"
    config.write_text(
        "config_version: 3\n"
        "project:\n  name: t\n  workspace: work/t\n"
        "source:\n  kind: static_html\n  seeds: [https://example.com/]\n"
        f"extract:\n  mode: {mode}\n  fields:\n"
        f"    title:\n      selector: {selector!r}\n      type: css\n",
        encoding="utf-8",
    )
    return config


@pytest.mark.parametrize("bad", ["div[[[[broken", ":::bad", "[unclosed", "div > "])
def test_malformed_selector_fails_at_config_load(tmp_path: Path, bad: str) -> None:
    """核心判据：语法错误的选择器必须**加载配置时**就失败。"""
    from omnicrawler.core.config import ConfigParseError, load_config

    config = _write_config(tmp_path, bad)
    with pytest.raises(ConfigParseError) as excinfo:
        load_config(config)
    message = str(excinfo.value)
    assert "extract.fields.title" in message, f"错误没点名是哪个字段：{message}"
    assert "CSS 选择器语法错误" in message, f"错误没说清是什么问题：{message}"


@pytest.mark.parametrize(
    "good", ["div", "div.item", "a[href^='http']", "ul > li:first-child", "#main .x"]
)
def test_valid_selectors_still_load(tmp_path: Path, good: str) -> None:
    """反方向：正常选择器不许被误伤。"""
    from omnicrawler.core.config import load_config

    config = _write_config(tmp_path, good)
    loaded = load_config(config)
    assert loaded.section("extract").get("fields"), good


def test_json_mode_uses_jsonpath_rules_not_css(tmp_path: Path) -> None:
    """只有 `mode: html` 才按 CSS 校验 —— JSON 模式的 selector 是 JSONPath。

    `$..title` 两边都非法，但**报错措辞必须不同**。这条断言 JSON 模式走的是
    JSONPath 那条判据。
    """
    from omnicrawler.core.config import ConfigParseError, load_config

    config = _write_config(tmp_path, "$..title", mode="json")
    with pytest.raises(ConfigParseError) as excinfo:
        load_config(config)
    message = str(excinfo.value)
    assert "JSONPath" in message, f"JSON 模式却报成 CSS 问题：{message}"


def test_selector_is_valid_agrees_with_the_engine() -> None:
    """单一真源：`selector_is_valid` 与 `select_nodes` 用同一条编译路径。

    断言方式：对每个样本，「能编译」与「取节点不抛」结果一致。
    """
    if str(_REPO / "src") not in sys.path:
        sys.path.insert(0, str(_REPO / "src"))
    from omnicrawler.extraction.html_tools import (
        parse_html,
        select_nodes,
        selector_is_valid,
    )

    document = parse_html("<html><body><div class='item'>x</div></body></html>")
    for selector in ("div", "div.item", "div[[[[broken", ":::bad", "[unclosed"):
        valid = selector_is_valid(selector)
        try:
            select_nodes(document, selector)
            engine_ok = True
        except Exception:  # noqa: BLE001 - 引擎抛什么不重要，只要与判定一致
            engine_ok = False
        assert valid is engine_ok, (
            f"{selector!r}: selector_is_valid={valid} 但引擎可用性={engine_ok}"
        )


def test_config_module_still_does_not_pull_bs4() -> None:
    """加载**不带 CSS 字段**的配置时，core.config 不该拉 bs4。

    这条守住「函数内导入」的意图：只在 HTML 模式且真有 fields 时才付那笔成本。
    必须用子进程 —— 测试进程里早就 import 过 bs4。
    """
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import omnicrawler.core.config; print('bs4' in sys.modules)",
        ],
        cwd=_REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert result.returncode == 0, result.stderr[-400:]
    assert result.stdout.strip() == "False", (
        f"core.config 把 bs4 拖进来了：{result.stdout.strip()}"
    )
