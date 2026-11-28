r"""坏正则必须在**配置校验**阶段就报（2026-11-28）。

## 缺陷

`safe_regex_search` 在正则编译失败时只记一条 `LOGGER.warning` 并返回 `None`。
那个 warning 用户在 GUI 里看不到、在 CLI 里被日志淹没。

症状与根因离得很远：

```
用户看到：字段「价格」一直是空的
真实原因：regex: "\d+(\.\d+" 少了一个右括号
```

## 判据

就是 `re.compile` 本身。不做启发式、**不管 ReDoS** —— 那是运行期防护的事
（`safe_regex_search` 的 `_unsafe_regex`），这里只回答「这串能编译吗」。

两件事必须分开：

| 问题 | 何时报 | 为什么 |
|---|---|---|
| 语法错 | **配置校验** | 写错了，与数据无关 |
| 病态模式（ReDoS）| 运行期 | 取决于**输入文本**，配置阶段不知道 |
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _write_config(tmp_path: Path, regex: str, *, mode: str = "html") -> Path:
    config = tmp_path / "c.yaml"
    config.write_text(
        "config_version: 3\n"
        "project:\n  name: t\n  workspace: work/t\n"
        "source:\n  kind: static_html\n  seeds: [https://example.com/]\n"
        f"extract:\n  mode: {mode}\n  fields:\n"
        "    price:\n      selector: span.price\n"
        f"      regex: {regex!r}\n",
        encoding="utf-8",
    )
    return config


@pytest.mark.parametrize(
    "bad",
    ["[unclosed", "(?P<x", "*invalid", "a{2,1}", "\\d+(\\.\\d+"],
)
def test_malformed_regex_fails_at_config_load(tmp_path: Path, bad: str) -> None:
    """★ 核心判据：语法错的正则必须**加载配置时**就失败。"""
    from omnicrawler.core.config import ConfigParseError, load_config

    config = _write_config(tmp_path, bad)
    with pytest.raises(ConfigParseError) as excinfo:
        load_config(config)
    message = str(excinfo.value)
    assert "extract.fields.price" in message, f"错误没点名是哪个字段：{message}"
    assert "正则语法错误" in message, f"错误没说清是什么问题：{message}"


@pytest.mark.parametrize(
    "good",
    ["^\\d+$", "\\d+\\.\\d+", "(?i)abc", "a|b", "\\s*,\\s*"],
)
def test_valid_regexes_still_load(tmp_path: Path, good: str) -> None:
    """★ 反方向：正常正则不许被误伤。"""
    from omnicrawler.core.config import load_config

    config = _write_config(tmp_path, good)
    loaded = load_config(config)
    assert loaded.section("extract").get("fields"), good


def test_regex_check_is_independent_of_mode(tmp_path: Path) -> None:
    """★ 正则与 mode 无关：JSON 模式也常配 regex 清洗日期/金额。"""
    from omnicrawler.core.config import ConfigParseError, load_config

    config = _write_config(tmp_path, "[unclosed", mode="json")
    with pytest.raises(ConfigParseError, match="正则语法错误"):
        load_config(config)


def test_redos_patterns_are_not_rejected_at_config_time(tmp_path: Path) -> None:
    """★ 关键边界：**病态但合法**的正则不该在配置阶段被拒。

    `(a+)+$` 能编译，是否危险取决于**输入文本**——配置阶段不知道。
    把它拒了会让合法配置无法保存；真正的防护在运行期的 `_unsafe_regex`。
    """
    from omnicrawler.core.config import load_config

    config = _write_config(tmp_path, "(a+)+$")
    loaded = load_config(config)
    assert loaded.section("extract").get("fields")


def test_runtime_guard_still_blocks_unsafe_patterns() -> None:
    """★ 对照：运行期防护仍在（配置阶段放行 ≠ 运行期不防）。

    这条确保「配置校验加了之后，没有顺手把 `_unsafe_regex` 关掉」。
    """
    import sys

    repo = Path(__file__).resolve().parents[3]
    if str(repo / "src") not in sys.path:
        sys.path.insert(0, str(repo / "src"))
    from omnicrawler.core.safe_data import safe_regex_search

    assert safe_regex_search("(a+)+$", "aaaa") is None, "病态正则没被运行期拦住"
