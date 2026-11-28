"""坏 `follow_xpath` 必须在**配置校验**阶段就报（2026-11-28）。

## 缺陷

`follow_xpath` 的消费者是 `sources.py` 里的 `root.xpath(follow_xpath)`，写错只会
在**链接发现阶段**抛 `XPathEvalError`。那时已经抓了几页，用户看到的是
「爬到一半失败」或「只抓到首页」。

```
旧：load_config(follow_xpath: //a[)  → 成功
    链接发现时 → XPathEvalError: Invalid expression

新：load_config(follow_xpath: //a[)  → ConfigParseError
      [1] source.follow_xpath: XPath 语法错误 '//a['
```

## 判据：只编译，不求值

`etree.XPath(value)` 只编译 —— 求值需要文档，而配置阶段没有文档。
编译失败 = 语法失败，这类判据不产生假阳性。

## ⚠️ 关键约束：lxml **不是**基础依赖

基础依赖只有 `PyYAML` + `defusedxml`（见 `pyproject.toml`）。lxml 在 `html` / `gui`
extra 里。所以校验必须**优雅降级**：

- 装了 lxml ⇒ 真校验，语法错误报 ConfigParseError；
- 没装 lxml ⇒ **跳过校验**，不让配置加载失败。

后者是对的：没装 lxml 时 `follow_xpath` 本来也用不了，`sources.py` 会在
真正要抓链接时报 ImportError —— 那时语境是可理解的。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]


def _write_config(tmp_path: Path, follow_xpath: str | None) -> Path:
    config = tmp_path / "c.yaml"
    body = (
        "config_version: 3\n"
        "project:\n  name: t\n  workspace: work/t\n"
        "source:\n  kind: static_html\n  seeds: [https://example.com/]\n"
    )
    if follow_xpath is not None:
        body += f"  follow_xpath: {follow_xpath!r}\n"
    config.write_text(body, encoding="utf-8")
    return config


@pytest.mark.parametrize("bad", ["//a[", "div[[[", "(((", "//div[@x="])
def test_malformed_xpath_fails_at_config_load(tmp_path: Path, bad: str) -> None:
    """★ 核心判据：语法错的 XPath 必须**加载配置时**就失败。"""
    pytest.importorskip("lxml")
    from omnicrawler.core.config import ConfigParseError, load_config

    config = _write_config(tmp_path, bad)
    with pytest.raises(ConfigParseError) as excinfo:
        load_config(config)
    message = str(excinfo.value)
    assert "source.follow_xpath" in message, f"错误没点名是哪个键：{message}"
    assert "XPath 语法错误" in message, f"错误没说清是什么问题：{message}"


@pytest.mark.parametrize(
    "good", ["//a/@href", "//div/a", "//*[@id='next']", "/html/body/a"]
)
def test_valid_xpath_still_loads(tmp_path: Path, good: str) -> None:
    """★ 反方向：正常 XPath 不许被误伤。"""
    pytest.importorskip("lxml")
    from omnicrawler.core.config import load_config

    config = _write_config(tmp_path, good)
    loaded = load_config(config)
    assert loaded.section("source").get("follow_xpath") == good


def test_missing_follow_xpath_is_fine(tmp_path: Path) -> None:
    """未配置时不该触发任何校验。"""
    from omnicrawler.core.config import load_config

    config = _write_config(tmp_path, None)
    loaded = load_config(config)
    assert not loaded.section("source").get("follow_xpath")


def test_validation_is_skipped_gracefully_without_lxml(tmp_path: Path) -> None:
    """★ 关键：**没装 lxml 时不许让配置加载失败**。

    lxml 在 `html` / `gui` extra 里，不是基础依赖。屏蔽它之后配置仍必须能加载 ——
    否则「最小安装」的用户连 `validate` 都跑不了。
    """
    config = _write_config(tmp_path, "//a[")
    blocker = (
        "import sys\n"
        "class _Block:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'lxml' or name.startswith('lxml.'):\n"
        "            raise ImportError('blocked by test')\n"
        "        return None\n"
        "sys.meta_path.insert(0, _Block())\n"
        "from omnicrawler.core.config import load_config\n"
        f"loaded = load_config(r'{config}')\n"
        "print('LOADED', bool(loaded.section('source').get('follow_xpath')))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", blocker],
        cwd=_REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert result.returncode == 0, f"没装 lxml 时配置加载失败了：{result.stderr[-500:]}"
    assert "LOADED True" in result.stdout, result.stdout[-300:]

def test_validator_lives_in_a_leaf_module_without_cycles() -> None:
    """★ 架构判据：判定逻辑放在 `sources/xpath_syntax.py`（叶子模块）。

    为什么不到处放：

    - `core/config.py` 不能 import `lxml`（可选依赖，`core` 必须 import-safe）；
    - `core/config.py` 也不能 import `sources/sources.py`（那会成环）。

    叶子模块两个问题都避开：不导入任何 `omnicrawler` 模块，`lxml` 函数内惰性导入。
    """
    import ast

    leaf = _REPO / "src" / "omnicrawler" / "sources" / "xpath_syntax.py"
    assert leaf.is_file(), "叶子模块不存在"
    tree = ast.parse(leaf.read_text(encoding="utf-8"))

    internal_imports: list[str] = []
    external_top_level: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.level > 0:
            internal_imports.append(f"relative level {node.level}")
        if isinstance(node, ast.Import):
            external_top_level.extend(alias.name for alias in node.names)

    assert not internal_imports, f"叶子模块不许有相对导入（会引入环）：{internal_imports}"
    assert "lxml" not in external_top_level, (
        "lxml 必须函数内惰性导入 —— 顶层导入会让最小安装的用户无法 validate"
    )


def test_core_config_does_not_import_lxml_or_sources_sources() -> None:
    """★ 双向确认：`core/config.py` 只引叶子模块，不碰 lxml 与 sources.sources。"""
    import ast

    tree = ast.parse((_REPO / "src" / "omnicrawler" / "core" / "config.py").read_text(encoding="utf-8"))
    external: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            external.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            external.add(node.module.split(".", 1)[0])
    assert "lxml" not in external, "core/config.py 顶层或函数内导入了 lxml"

    source = (_REPO / "src" / "omnicrawler" / "core" / "config.py").read_text(encoding="utf-8")
    assert "xpath_syntax" in source, "没有引用叶子模块"
    assert "from ..sources.sources import" not in source, "引了 sources.sources（会成环）"
