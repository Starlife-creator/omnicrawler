"""S4.1：包根惰性化——import 毫秒级 + 不 eager 加载 + 兼容路径可用。"""

from __future__ import annotations

import subprocess
import sys


def test_import_omnicrawl_is_fast_and_lazy() -> None:
    """import omnicrawl 不再 eager 导入全部兼容模块（原约 287ms）。"""
    code = (
        "import sys, time\n"
        "start = time.perf_counter()\n"
        "import omnicrawl\n"
        "elapsed = (time.perf_counter() - start) * 1000\n"
        "heavy = [m for m in sys.modules if m.startswith('omnicrawl.') and m.count('.') == 1]\n"
        "print(round(elapsed, 1), len(heavy))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=30,
        cwd=None,
    )
    assert result.returncode == 0, result.stderr
    elapsed_ms, submodules = result.stdout.split()
    assert float(elapsed_ms) < 150.0, f"import omnicrawl 耗时 {elapsed_ms}ms（阈值 150ms）"
    assert int(submodules) == 0, f"eager 加载了 {submodules} 个顶层子模块"


def test_compat_imports_still_work() -> None:
    """旧路径 import 仍可用（惰性重定向）。"""
    from omnicrawl.ai_providers import build_provider  # noqa: F401
    from omnicrawl.config import AppConfig  # noqa: F401
    from omnicrawl.errors import ExtractionError  # noqa: F401
    from omnicrawl.state import StateStore  # noqa: F401
    from omnicrawl.utils import utcnow  # noqa: F401

    assert callable(build_provider)
    assert callable(utcnow)
    assert ExtractionError.__name__ == "ExtractionError"


def test_compat_attribute_access_works() -> None:
    import omnicrawl

    assert omnicrawl.AppConfig is not None
    assert omnicrawl.StateStore is not None
    # 属性访问触发惰性加载（不 eager）
    assert callable(omnicrawl.utils.utcnow)


def test_real_subpackages_are_not_shadowed() -> None:
    """map 中与真实子包同名的键（quality/utils/state）不被 finder 拦截。"""
    import omnicrawl.quality  # noqa: F401
    import omnicrawl.state  # noqa: F401
    import omnicrawl.utils  # noqa: F401

    assert omnicrawl.quality is not None
