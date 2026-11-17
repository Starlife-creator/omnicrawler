"""facade 转发型入口：参数必须真的送达插件，且 operation 分支存在。

## 为什么需要这个守卫

facade 的转发型入口都是这个形状：

```python
def run_pdf_extraction(*args, **kwargs):
    module = require_document("pdf.extract")
    return module.run_operation("pdf.extract", {"args": list(args), "kwargs": dict(kwargs)})
```

有三个环节会静默出错，而且**"能 import"全都测不出来**：

1. `run_operation` 里没有这个 operation 分支 ⇒ 抛 `KeyError`；
2. 插件不做 `args`/`kwargs` 解包 ⇒ 参数被当成 payload 的两项；
3. 入口本身接错（返回模块当函数用，见 `test_facade_entry_contracts.py`）。

## 判据

对每个转发型入口，断言它**走到了插件里**——表现是抛业务异常
（参数不合法、路径不存在等），而**不是** `AttributeError` / `NameError` /
`ImportError` / `ModuleNotFoundError` / 缺分支的 `KeyError`。

业务异常是**成功信号**：说明参数被正确解包并送进了插件。

`SystemExit` 也算通——argparse 解析到参数了（`--help` 或非法选项）。

插件未安装时整体跳过。
"""

from __future__ import annotations

import pytest

from omnicrawler.services import document_facade

#: 出现即说明"入口接错了"，不是"参数不对"。
FATAL = (AttributeError, NameError, ImportError, ModuleNotFoundError)


def _require_plugin() -> None:
    if not document_facade.document_capability()["available"]:
        pytest.skip("需要 document-workbench 插件")


def _assert_reaches_plugin(name: str, *args: object) -> None:
    """调用 facade 入口，断言它走到了插件（抛业务异常或 SystemExit 均可）。"""
    entry = getattr(document_facade, name)
    try:
        entry(*args)
    except FATAL:
        raise
    except KeyError as exc:
        text = str(exc)
        # 缺 operation 分支的 KeyError 形态：`'pdf.xxx'` / 含 operation 字样
        assert "." not in text.strip("'\"") and "operation" not in text.lower(), (
            f"{name}: KeyError {text} —— 插件 run_operation 缺这个分支"
        )
    except SystemExit:
        pass  # argparse 已解析参数
    except Exception:  # noqa: BLE001 - 业务异常 = 入口通
        pass


@pytest.mark.parametrize(
    "name,args",
    [
        ("pdf_main", (["__no_such_stage__"],)),
        ("pdf_processor_main", (["__no_such_stage__"],)),
        ("field_extractor_main", (["__no_such_stage__"],)),
        ("pdf_database_status", ("",)),
        ("apply_pdf_review", (None, None)),
        ("run_pdf_extraction", ("",)),
        ("run_pdf_processing", ("",)),
        ("create_project_config", ("builtin:pdf/generic_template.yaml",)),
        ("convert_with_preset", ("a.csv", "b.jsonl")),
        ("convertx_preset", ("nope",)),
    ],
)
def test_forwarding_entry_reaches_plugin(name: str, args: tuple[object, ...]) -> None:
    """转发型入口必须把参数送到插件，不能停在 facade 层。"""
    _require_plugin()
    _assert_reaches_plugin(name, *args)


def test_parse_document_reports_format_error_not_import_error() -> None:
    """`parse_document` 对无扩展名路径应报**格式**错误，不是找不到模块。"""
    _require_plugin()
    with pytest.raises(Exception) as excinfo:
        document_facade.parse_document("no-extension")
    assert not isinstance(excinfo.value, FATAL), (
        f"parse_document 抛了 {type(excinfo.value).__name__}：入口没接到插件"
    )
    assert "不支持" in str(excinfo.value) or "格式" in str(excinfo.value), (
        f"错误信息不可读：{excinfo.value!r}"
    )


def test_every_operation_used_by_facade_exists_in_plugin() -> None:
    """facade 里出现的每个 operation 字符串，插件 `run_operation` 都要有分支。

    静态检查（读源码），不需要真的调用——它能提前抓到"facade 加了新转发
    但插件没实现"这种不一致。
    """
    _require_plugin()
    import re
    from pathlib import Path

    plugin_dir = document_facade._plugin_dir()
    assert plugin_dir is not None, "插件目录解析失败"
    plugin_py = Path(plugin_dir) / "plugin.py"
    assert plugin_py.is_file(), f"插件入口不存在: {plugin_py}"

    plugin_src = plugin_py.read_text(encoding="utf-8")
    handled = set(re.findall(r'operation\s*==\s*"([^"]+)"', plugin_src))
    assert handled, "插件里没找到任何 operation 分支——解析规则可能过期了"

    facade_src = Path(document_facade.__file__).read_text(encoding="utf-8")
    used = set(re.findall(r'run_operation\(\s*"([^"]+)"', facade_src))
    assert used, "facade 里没找到 run_operation 调用"

    missing = sorted(used - handled)
    assert not missing, (
        f"facade 调用但插件没有分支的 operation: {missing}"
        f"（插件已实现: {sorted(handled)}）"
    )
