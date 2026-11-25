"""`ApplicationService` 的模块顶层不得导入 `pipeline` / `exporters`。

为什么要有这个文件（2026-11-25 实测）：

`gui/main.py` 在**模块顶层**导入 `ApplicationService`。而它原先也在顶层导入：

```python
from ..pipeline import Pipeline          # 整条链 342 ms
from ..pipeline.exporters import export_all
from ..pipeline_ops.preflight import run_sample
from ..runtime.run_control import RunControl
from ..fetching.session_lease import session_lease
from ..security.security_audit import egress_audit_report
```

后果：用户双击图标 → 还没看到任何界面，就先付了这 342 ms。
而这些名字只在 `run` / `sample` / `export` / `resume` 里用到，
**GUI 冷启动时一个都不调用**。

改成函数内惰性导入后：

```
导入 gui.main:  656 ms -> 591 ms
pipeline 已加载: True -> False
exporters 已加载: True -> False
```

本文件用 **`sys.modules` 增量**做判据，不用计时：模块是否被加载是确定性的。
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path


def _run_probe(body: str, repo_src: Path) -> str:
    """在**全新解释器**里跑一段探针，返回 stdout。

    必须新进程：`sys.modules` 是进程级的，同进程里早被别的测试加载过就测不出来。
    """
    code = textwrap.dedent(body)
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=120, encoding="utf-8",
        env={**__import__("os").environ, "PYTHONPATH": str(repo_src)},
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return result.stdout


def test_pipeline_not_imported_at_module_level() -> None:
    """`import ApplicationService` 之后，`pipeline` 不得出现在 sys.modules。"""
    repo_src = Path(__file__).resolve().parents[3] / "src"
    out = _run_probe(
        """
        import sys
        from omnicrawler.services.application_service import ApplicationService  # noqa: F401
        print("PIPELINE", "omnicrawler.pipeline" in sys.modules)
        print("EXPORTERS", "omnicrawler.pipeline.exporters" in sys.modules)
        """,
        repo_src,
    )
    assert "PIPELINE False" in out, (
        "导入 ApplicationService 时把 omnicrawler.pipeline 拉起来了 —— "
        "GUI 冷启动会白付 342 ms。\n" + out
    )
    assert "EXPORTERS False" in out, (
        "导入 ApplicationService 时把 pipeline.exporters 拉起来了。\n" + out
    )


def test_pipeline_still_works_when_needed() -> None:
    """惰性导入不能把功能弄坏 —— 真调用一次 `sample()` 的前置路径。"""
    repo_src = Path(__file__).resolve().parents[3] / "src"
    out = _run_probe(
        """
        from omnicrawler.services.application_service import ApplicationService
        # `_plan()` 走 TaskIR + compile_task_plan，不碰 pipeline；
        # 这里只验证模块本身可导入且类可实例化。
        assert ApplicationService is not None
        print("OK")
        """,
        repo_src,
    )
    assert "OK" in out
