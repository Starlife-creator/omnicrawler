"""omnicrawler.sources 的包级导入不许拖进 bs4（2026-11-28）。

## 缺陷

sources/__init__.py 原本写着 from .sources import GenericSource。
而 sources/sources.py 是重模块：defusedxml / extraction.extractors /
extraction.html_tools（连带 bs4）/ fetching.http_client。

于是**任何** omnicrawler.sources.* 的导入都会拉起整条链 —— 包括只需要
sources.kinds 里那 22 个字符串的 gui/core/validator.py。

实测（子进程 5 次取最小值）：

| 导入 | 修前 | 修后 |
|---|---|---|
| gui.core.validator | 245.8 ms | 101.9 ms（-59%）|
| gui.main（整体）| 591.8 ms | 569.1 ms |

## 判据

包 __init__ 的导入会被**每一个子模块**继承。所以它只该放「几乎每个消费者都要」
的东西 —— 而 GenericSource 只有 site_adapters 与 pipeline/registry 用。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]


def _probe(code: str) -> str:
    """在全新解释器里跑，返回 stdout。

    必须用子进程：测试进程里早就 import 过 bs4，sys.modules 断言恒真。
    """
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=_REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert result.returncode == 0, result.stderr[-400:]
    return result.stdout.strip()


def test_importing_source_kinds_does_not_pull_bs4() -> None:
    """核心判据：只读白名单常量不许拉起 DOM 解析栈。"""
    out = _probe(
        "import sys; import omnicrawler.sources.kinds; "
        "print('bs4' in sys.modules)"
    )
    assert out == "False", "sources/__init__ 又把 GenericSource 拖进来了"


def test_importing_source_kinds_does_not_pull_extraction() -> None:
    """同类：extraction.html_tools 是 bs4 的上游，一并断言。"""
    out = _probe(
        "import sys; import omnicrawler.sources.kinds; "
        "print(any(k.startswith('omnicrawler.extraction') for k in sys.modules))"
    )
    assert out == "False"


def test_generic_source_still_importable_from_the_package() -> None:
    """反方向：from omnicrawler.sources import GenericSource 必须照旧可用。

    惰性化不能变成「删掉导出」—— 那会破坏既有调用方。
    """
    out = _probe(
        "from omnicrawler.sources import GenericSource; "
        "print(GenericSource.__name__)"
    )
    assert out == "GenericSource"


def test_generic_source_is_still_the_real_class() -> None:
    """惰性取到的必须是真的那个类（不是替身）。"""
    out = _probe(
        "from omnicrawler.sources import GenericSource; "
        "from omnicrawler.sources.sources import GenericSource as Direct; "
        "print(GenericSource is Direct)"
    )
    assert out == "True"


def test_unknown_attribute_raises_attribute_error() -> None:
    """__getattr__ 必须对陌生名字抛 AttributeError。

    返回 None 会让 hasattr(x, "typo") 恒真 —— 那是更隐蔽的缺陷。
    """
    out = _probe(
        "import omnicrawler.sources as s\n"
        "try:\n"
        "    s.no_such_thing\n"
        "except AttributeError:\n"
        "    print('AttributeError')\n"
        "else:\n"
        "    print('NO-RAISE')\n"
    )
    assert out == "AttributeError"


def test_probe_can_actually_detect_bs4() -> None:
    """自检：探针必须能看见 bs4 —— 否则上面的 False 是假绿。"""
    out = _probe("import sys, bs4; print('bs4' in sys.modules)")
    assert out == "True"
