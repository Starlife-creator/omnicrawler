"""pytest marker 声明与使用的契约（批次 2「判据可信」）。

由来：`pyproject.toml` 的 `[tool.pytest.ini_options].markers` 此前只是**声明**，
既不校验使用、也不启用 `--strict-markers`。后果是两类静默失效：

1. **拼错的 marker 不报错**：`@pytest.mark.netwrok` 会被 pytest 当作一个全新的
   marker 静默接受，测试照常跑、门禁照常绿，而「按 marker 选择/跳过」的意图
   完全落空——这正是「判据看起来存在、实际不生效」的典型形态。
2. **声明了却没人用 / 用了却没声明**：marker 表与实际用法逐渐漂移，
   而漂移不会触发任何红灯。

本文件把三件事变成会失败的检查：
* `addopts` 里必须启用 `--strict-markers`（否则上面第 1 类问题回来）；
* 声明的每个 marker 都必须真的被使用（防止「声明即完成」的空账）；
* 使用到的每个 marker 都必须在 `markers` 表里声明。
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PYPROJECT = REPO_ROOT / "pyproject.toml"

# pytest 内置 marker（无需在 `markers` 里声明）＋由插件提供的 marker。
# 内置清单见 pytest 文档 "Working with custom markers" 的 Builtin markers 一节。
_EXTERNAL_MARKERS = frozenset(
    {
        "asyncio",  # pytest-asyncio 提供
        "parametrize",
        "skip",
        "skipif",
        "xfail",
        "usefixtures",
        "filterwarnings",
    }
)


def _pytest_ini() -> dict[str, object]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return data["tool"]["pytest"]["ini_options"]


def _declared_markers() -> set[str]:
    declared: set[str] = set()
    for entry in _pytest_ini().get("markers", []):
        name = str(entry).split(":", 1)[0].strip()
        if name:
            declared.add(name)
    return declared


def _used_markers() -> dict[str, list[str]]:
    """扫 `tests/` 与 `e2e/` 下的 marker 用法（返回 marker -> 文件列表）。

    用 AST 而不是正则：注释与 docstring 里也会出现 `pytest.mark.xxx` 的字样
    （本文件的 docstring 就故意写了拼错的 `netwrok` 作反例），正则会把它们
    当成真实用法，导致契约自我误报。
    """
    used: dict[str, list[str]] = {}
    for base in ("tests", "e2e"):
        root = REPO_ROOT / base
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:  # pragma: no cover - 语法错的文件由 pytest 自己报
                continue
            for node in ast.walk(tree):
                # 命中 `pytest.mark.<name>` 的 Attribute 链
                if not isinstance(node, ast.Attribute):
                    continue
                value = node.value
                if (
                    isinstance(value, ast.Attribute)
                    and value.attr == "mark"
                    and isinstance(value.value, ast.Name)
                    and value.value.id == "pytest"
                ):
                    used.setdefault(node.attr, []).append(str(path.relative_to(REPO_ROOT)))
    return used


def test_strict_markers_is_enabled() -> None:
    """必须启用 `--strict-markers`：拼错的 marker 要报错，而不是静默新建一个。"""
    addopts = _pytest_ini().get("addopts", "")
    assert "--strict-markers" in str(addopts), (
        "pyproject.toml 的 [tool.pytest.ini_options].addopts 必须包含 --strict-markers；"
        "否则 `@pytest.mark.netwrok` 这类拼写错误会被静默接受，"
        "「按 marker 选择/跳过」的判据看起来存在、实际不生效"
    )


def test_every_declared_marker_is_actually_used() -> None:
    """声明的 marker 必须真被使用——「声明了」不等于「生效了」。"""
    declared = _declared_markers()
    assert declared, "markers 表为空，说明解析失败或配置被误删"
    used = _used_markers()
    unused = sorted(declared - set(used) - _EXTERNAL_MARKERS)
    assert not unused, (
        f"这些 marker 在 markers 表里声明了但全仓无人使用：{unused}。"
        "要么删掉声明（避免「声明即完成」的空账），要么补上真正的用法"
    )


def test_every_used_marker_is_declared() -> None:
    """使用到的 marker 必须已声明——未声明的在 --strict-markers 下会直接报错。"""
    used = _used_markers()
    assert used, "扫描不到任何 marker 用法，说明扫描逻辑失效（空集不能当通过）"
    undeclared = sorted(set(used) - _declared_markers() - _EXTERNAL_MARKERS)
    assert not undeclared, (
        f"这些 marker 被使用但未在 markers 表声明：{undeclared}——"
        "在 --strict-markers 下会直接判错"
    )


def test_network_marker_description_matches_reality() -> None:
    """`network` 的说明必须与现实一致：实际由用例内 `skipif` 兜底，不是 `-m` 过滤。

    实测：`network` 只出现在 `tests/unit/plugin/test_market_client_remote.py`，
    且那里同时挂着 `skipif(not os.environ.get("OMNICRAWL_TEST_LIVE_MARKET"))`。
    配置里既没有 `addopts = -m "not network"` 也没有 CI 的 `-m` 过滤，所以
    旧文案「enable via env or -m network」的后半句是**不成立的承诺**。
    """
    entries = [str(item) for item in _pytest_ini().get("markers", [])]
    network = next((item for item in entries if item.startswith("network:")), None)
    assert network is not None, "markers 表里没有 network 声明"
    assert "OMNICRAWL_TEST_LIVE_MARKET" in network, (
        "network marker 的说明必须写明真实开关是环境变量 "
        "OMNICRAWL_TEST_LIVE_MARKET（用例内 skipif 实现），而不是 -m 过滤"
    )


def test_reverse_assertion_undeclared_marker_is_detectable() -> None:
    """**反向断言**：若有人新增一个未声明的 marker，本契约必须能发现它。

    直接构造一份「使用集合里有未声明项」的样本，断言差集非空——
    保证上面的 `test_every_used_marker_is_declared` 不是恒真式。
    """
    declared = _declared_markers()
    used = set(_used_markers()) | {"brand_new_marker_nobody_declared"}
    assert sorted(used - declared - _EXTERNAL_MARKERS) == ["brand_new_marker_nobody_declared"]
