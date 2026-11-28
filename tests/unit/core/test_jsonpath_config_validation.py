"""JSONPath 语法错误必须在**配置校验**阶段就报出来（2026-11-28）。

## 缺陷

`path: $..title` 此前能通过 `omnicrawler validate`，直到真正跑采集才抛
`ValueError` —— 那时用户已经在等结果，而且报错位置在抽取器里，看不出是
「配置写错了」。

```
旧：load_config(带 $..title 的配置)  → 成功
    采集时 → ValueError: JSONPath '$..title' 使用了不支持的语法…

新：load_config(带 $..title 的配置)  → ConfigParseError
      [1] extract.fields.title: JSONPath '$..title' 使用了不支持的语法…
```

## 判据为什么放在叶子模块

配置校验是 `validate` / GUI 加载 / 模板渲染的必经之路，不能为一个字符串检查
拉进 bs4 / lxml。所以判定逻辑移到 `core/json_path_syntax.py`（无依赖），
`extraction.extractors` 与 `core.config` **读同一份登记表** ——
不会「校验说行、运行时说不行」。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]


def _write_config(tmp_path: Path, field_path: str, *, mode: str = "json") -> Path:
    config = tmp_path / "c.yaml"
    config.write_text(
        "config_version: 3\n"
        "project:\n  name: t\n  workspace: work/t\n"
        "source:\n  kind: rest\n  seeds: [https://api.example.com/x]\n"
        f"extract:\n  mode: {mode}\n  item_path: '$.items[*]'\n  fields:\n"
        f"    title:\n      path: {field_path!r}\n",
        encoding="utf-8",
    )
    return config


def test_recursive_descent_fails_at_config_load(tmp_path: Path) -> None:
    """★ 核心判据：`$..title` 必须**加载配置时**就失败。

    此前它会一路通过 `validate`，直到采集才炸。
    """
    from omnicrawler.core.config import ConfigParseError, load_config

    config = _write_config(tmp_path, "$..title")
    with pytest.raises(ConfigParseError) as excinfo:
        load_config(config)
    message = str(excinfo.value)
    assert "extract.fields.title" in message, f"错误没点名是哪个字段：{message}"
    assert "递归搜索" in message, f"错误没说清怎么改：{message}"


def test_filter_expression_fails_at_config_load(tmp_path: Path) -> None:
    from omnicrawler.core.config import ConfigParseError, load_config

    config = _write_config(tmp_path, "$.items[?(@.x>1)]")
    with pytest.raises(ConfigParseError, match="过滤条件"):
        load_config(config)


def test_valid_json_paths_still_load(tmp_path: Path) -> None:
    """★ 反方向：正常路径不许被误伤。"""
    from omnicrawler.core.config import load_config

    for index, good in enumerate(("title", "$.title", "$.data.items[*].title", "$.a.b[0]")):
        target = tmp_path / f"case{index}"
        target.mkdir(parents=True, exist_ok=True)
        config = _write_config(target, good)
        loaded = load_config(config)
        assert loaded.section("extract").get("fields"), good


def test_html_mode_is_not_subjected_to_json_path_rules(tmp_path: Path) -> None:
    """★ 只有 `mode: json` 才按 JSONPath 校验 —— HTML 模式的 selector 是 CSS。

    `..` 在 CSS 选择器里是合法的（如 `div.x .. y` 不是，但 `[class~="a"]` 等写法很多），
    总之**不能**拿 JSONPath 的判据去卡它。
    """
    from omnicrawler.core.config import load_config

    config = _write_config(tmp_path, "div.item", mode="html")
    loaded = load_config(config)
    assert loaded.section("extract").get("fields")


def test_the_two_consumers_share_one_registry() -> None:
    """★ 单一真源：`extractors` 与 `config` 用的是**同一个**登记表对象。"""
    if str(_REPO / "src") not in sys.path:
        sys.path.insert(0, str(_REPO / "src"))
    from omnicrawler.core import json_path_syntax
    from omnicrawler.extraction import extractors

    assert (
        extractors._UNSUPPORTED_JSON_PATH_SYNTAX
        is json_path_syntax.UNSUPPORTED_JSON_PATH_SYNTAX
    ), "两边各自维护登记表 ⇒ 迟早出现「校验说行、运行说不行」"


def test_config_module_does_not_pull_the_dom_stack() -> None:
    """★ 配置校验不许为了字符串检查拉进 bs4 / lxml。

    这条防的是「图省事直接 `from ..extraction.extractors import ...`」。
    必须用子进程：测试进程里早就 import 过 bs4。
    """
    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import omnicrawler.core.config; "
            "print(any(k.startswith('omnicrawler.extraction') for k in sys.modules), "
            "'bs4' in sys.modules)",
        ],
        cwd=_REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert result.returncode == 0, result.stderr[-400:]
    assert result.stdout.strip() == "False False", (
        f"core.config 把抽取栈拖进来了：{result.stdout.strip()}"
    )
