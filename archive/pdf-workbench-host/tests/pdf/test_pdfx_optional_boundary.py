"""条目 7 续：`pdfx` 可组件化的前置条件——宿主不得在**导入期**依赖它。

**为什么这是组件化的硬前提**

`pdfx` 若要作为可选组件分发，组件未安装时：
* 宿主必须能**正常启动**（不能因为 import 失败就崩）；
* 只有真正要用 PDF 功能时才报「PDF 组件未安装」，而且要是**可操作**的提示。

**当前真实状态（实测，写在这里防止将来退化）**

绝大多数调用点已经是函数内惰性导入——这是好消息，说明前人已经做对了一半：

```
apps/field_extractor.py:36-42     函数内
apps/pdf_processor.py:38-42       函数内
cli/_main.py:256                  函数内
commands/run_status.py:31-33      函数内
document_ir/pdf.py:24,60          函数内
gui/views/pdf_workbench*.py       函数内
pipeline_ops/pdf_integration.py   函数内
pipeline_ops/preflight.py:79      函数内
services/workbench.py:43-47       函数内
services/pdf_quality_benchmark.py:503  函数内
```

**唯一例外**：`services/pdf_quality_benchmark.py:47` 在**模块顶层**导入
``from ..pdfx.normalization import normalize_amount``。
这会让"只导入质量基准模块"也硬依赖 pdfx——组件缺失时连基准模块都进不来。

**判据**

1. 宿主源码（`src/omnicrawler/` 内、`pdfx/` 之外）**不得在模块顶层**导入 pdfx；
2. `normalize_amount` 这类**纯函数**应上提到宿主侧（`core/` 或 `quality/`），
   而不是让宿主反向依赖文档子系统；
3. 组件缺失时的报错必须**可操作**（说"装组件"而不是丢一个 ImportError 栈）。
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC = _REPO_ROOT / "src" / "omnicrawler"


def _top_level_pdfx_imports() -> list[str]:
    """宿主侧**模块顶层**导入 pdfx 的位置（pdfx 自己除外）。"""
    offenders: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        if "__pycache__" in path.parts or "pdfx" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in tree.body:  # 只看模块顶层
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
                # `from ..pdfx.x import y` 的 node.level>0，module 是 "pdfx.x"
            for module in modules:
                if module == "pdfx" or module.startswith("pdfx.") or ".pdfx" in module:
                    offenders.append(
                        f"{path.relative_to(_REPO_ROOT).as_posix()}:{node.lineno} -> {module}"
                    )
    return offenders


def test_host_does_not_import_pdfx_at_module_level() -> None:
    """宿主不得在模块顶层导入 pdfx——否则组件缺失时宿主起不来。"""
    offenders = _top_level_pdfx_imports()
    assert offenders == [], (
        "以下位置在模块顶层依赖了 pdfx：\n  " + "\n  ".join(offenders) +
        "\npdfx 组件化后未安装时，这些模块会直接 ImportError，"
        "用户连主界面都打不开。请改为函数内惰性导入，"
        "或把需要的纯函数上提到宿主侧（core/ 或 quality/）。"
    )


def test_normalize_amount_available_without_pdfx() -> None:
    """金额归一这类**纯函数**必须能在不导入 pdfx 的前提下使用。

    它是质量基准与通用后处理都要用的基础能力，不该绑在文档子系统上。
    """
    import subprocess
    import sys

    script = (
        "import sys\n"
        f"sys.path.insert(0, {str(_REPO_ROOT / 'src')!r})\n"
        "from omnicrawler.quality.amounts import normalize_amount\n"
        "loaded = [m for m in sys.modules if m.startswith('omnicrawler.pdfx')]\n"
        "print('PDFX_LOADED=' + repr(loaded))\n"
        "print('VALUE=' + repr(normalize_amount('12,345.67 元')))\n"
    )
    # 不用 capture_output：本沙箱禁命名管道（见 tests/unit/tools 里的同类记录）
    import tempfile

    out = Path(tempfile.mkdtemp()) / "probe.txt"
    with out.open("w", encoding="utf-8") as handle:
        result = subprocess.run(
            [sys.executable, "-c", script],
            stdout=handle, stderr=subprocess.STDOUT, timeout=120, check=False,
        )
    text = out.read_text(encoding="utf-8", errors="replace")
    assert result.returncode == 0, f"导入 normalize_amount 失败：\n{text[-1200:]}"
    assert "PDFX_LOADED=[]" in text, (
        "使用金额归一却把整个 pdfx 拉进来了：\n" + text[-800:]
    )
    assert "VALUE=" in text and "12345.67" in text, f"归一结果不对：\n{text[-400:]}"
