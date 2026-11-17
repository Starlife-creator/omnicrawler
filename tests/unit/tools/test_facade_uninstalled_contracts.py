"""卸载态契约：插件没装时，每个入口都必须给**可操作**提示。

## 为什么需要这个守卫

用户要求"组件化让用户安心安装"，反面就是：**没装插件时不能崩、不能装死**。

三种失败形态都会让用户困惑：

1. 抛裸 `ImportError` / `ModuleNotFoundError` —— 用户不知道要装什么；
2. 抛 `AttributeError` —— 看起来像程序坏了；
3. **静默成功**（返回 `None` / 空对象）—— 最坏，用户以为功能在，实际什么都没做。

正确行为是抛 `RuntimeError`，消息里点名 **`document-workbench`** 且给出安装指引。

## 实现方式

本测试**强制**把插件目录指到空位置（`_plugin_dir` 返回 `None`），
所以无论本机装没装插件，测的都是"卸载态"。

这条与 `test_facade_entry_contracts.py`（测"装了之后拿到什么"）
是互补的两侧。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.services import document_facade

#: 每个入口一组"能让它走到插件调用"的最小参数。
#:
#: **`table_io` 不在此列**：表格读写（csv/jsonl/xlsx/parquet/duckdb）已于
#: 2026-11-22 上提到宿主（`services/table_io.py`），`transform` 是支柱①的
#: 通用能力，不再依赖 PDF 插件。它在未装插件时必须**正常可用**——
#: 由 `test_table_io_works_without_plugin` 单独钉住。
#:
#: **`compare_runs` 也不在此列**：它同样已改回宿主实现（`review/run_compare.py`），
#: 属支柱①运维能力，不依赖插件。
ENTRIES: list[tuple[str, tuple[object, ...]]] = [
    ("load_pdf_config", ("c.yaml",)),
    ("create_project_config", ("builtin:pdf/generic_template.yaml",)),
    ("run_pdf_extraction", ("c.yaml",)),
    ("run_pdf_processing", ("c.yaml",)),
    ("pdf_database_status", ("c.sqlite",)),
    ("apply_pdf_review", (None, None)),
    ("pdf_main", ([],)),
    ("pdf_processor_main", ([],)),
    ("field_extractor_main", ([],)),
    ("convertx_module", ()),
    ("convert_with_preset", ("a.csv", "b.jsonl")),
    ("convertx_preset", ("x",)),
    ("parse_document", ("a.pdf",)),
    ("document_block_class", ()),
    ("ocr_backend_class", ()),
    ("open_pdf_database", ("c.sqlite",)),
    ("pdf_views", ()),
    ("pdf_region_selector", ()),
    ("run_operation", ("pdf.validate", {})),
]


@pytest.fixture
def uninstalled(monkeypatch: pytest.MonkeyPatch) -> None:
    """强制"插件未安装"：让目录解析永远返回 None。"""
    monkeypatch.setattr(document_facade, "_plugin_dir", lambda: None)


@pytest.mark.parametrize("name,args", ENTRIES)
def test_uninstalled_entry_gives_actionable_error(
    uninstalled: None, name: str, args: tuple[object, ...]
) -> None:
    """未装插件时：必须抛 RuntimeError，消息点名插件并给安装指引。"""
    entry = getattr(document_facade, name)
    with pytest.raises(RuntimeError) as excinfo:
        entry(*args)

    message = str(excinfo.value)
    assert message.strip(), f"{name}: 错误消息为空"
    assert "document-workbench" in message, (
        f"{name}: 消息没点名要装哪个插件，用户无法照做。实际: {message!r}"
    )
    assert "安装" in message or "OmniCrawler 市场" in message, (
        f"{name}: 消息没给安装指引。实际: {message!r}"
    )


def test_uninstalled_never_silently_succeeds(uninstalled: None) -> None:
    """未装插件时不得静默成功——那会让用户以为功能可用。

    逐个入口确认"要么抛 RuntimeError，要么抛别的异常"，**不能正常返回**。
    """
    for name, args in ENTRIES:
        entry = getattr(document_facade, name)
        try:
            result = entry(*args)
        except RuntimeError:
            continue  # 期望路径
        except Exception:  # noqa: BLE001 - 只要不是 RuntimeError 但也没静默返回，记录
            continue
        pytest.fail(
            f"{name} 在插件未安装时静默返回了 {type(result).__name__}"
            f"（值 {result!r}）——用户会以为功能可用"
        )


def test_capability_report_is_consistent_when_uninstalled(uninstalled: None) -> None:
    """能力报告与目录探测必须一致：都判不可用。"""
    report = document_facade.document_capability()
    assert report["available"] is False
    assert report["plugin_installed"] is False
    assert report["missing"], "缺失清单为空——用户不知道缺什么"
    assert "document-workbench" in report["reason"]


def test_migrated_packages_absent_regardless_of_plugin(uninstalled: None) -> None:
    """四个包必须已从宿主移除（与插件装没装无关）。"""
    absent = document_facade.migrated_packages_absent()
    assert all(absent.values()), f"仍有包留在宿主: {absent}"


def test_table_io_works_without_plugin(uninstalled: None, tmp_path: Path) -> None:
    """表格读写必须**不依赖** PDF 插件（支柱①的通用能力）。

    2026-11-22 前，`omnicrawler transform` 在未装 PDF 插件时完全不可用，
    还给出"主流程不受影响"的误导提示——因为表格读写随 `convertx`
    迁进了插件。现已上提到 `services/table_io.py`。

    这条测试钉住"没装插件也能用"，防止有人把它搬回插件里。
    """
    table_io = document_facade.table_io()
    assert table_io is not None, "未装插件时 table_io 返回 None"
    assert table_io.READERS, "未装插件时 READERS 为空"
    assert table_io.WRITERS, "未装插件时 WRITERS 为空"

    # 真的能读能写，不是只有注册表
    source = tmp_path / "in.csv"
    source.write_text("a,b\n1,2\n", encoding="utf-8")
    rows = table_io.READERS[".csv"](source, {})
    assert rows == [{"a": "1", "b": "2"}], f"读取结果异常: {rows}"

    target = tmp_path / "out.jsonl"
    table_io.WRITERS[".jsonl"](rows, target, {})
    assert target.is_file(), "写入失败"
    assert '"a": "1"' in target.read_text(encoding="utf-8")
