"""支柱边界：哪些 facade 入口**不该**依赖 PDF 插件。

## 这条守卫防的是什么

2026-11-22 连续发现两处同类误绑：

| 入口 | 后果 |
|---|---|
| `table_io` | `omnicrawler transform`（支柱①数据加工）未装插件时**完全不可用** |
| `compare_runs` | `omnicrawler compare-runs` 与删除审计同上 |

两者都把**通用能力**绑在了可选插件上。用户没装 PDF 插件，
却连爬虫主流程的配套命令都用不了——这违反"只保留两大主线支柱"。

## 判据分三类

1. **支柱①能力**：未装插件时必须可用（`table_io` / `compare_runs`）；
2. **PDF 工作台能力**：未装插件时必须给可操作提示（`pdf.*` / `convertx.*`）；
3. **插件机制自身**：只在插件加载路径上被调用（`ocr_backend_class`），
   依赖插件是对的。

第三类不在这里测——它没有"未装插件"的场景（那个入口本身就是
为插件服务的）。
"""

from __future__ import annotations

import inspect

import pytest

from omnicrawler.services import document_facade

#: 支柱①能力：**不得**依赖 PDF 插件。
#: (名称, 说明)
_PILLAR_ONE_ENTRIES = [
    ("table_io", "表格读写：omnicrawler transform 的底层"),
    ("compare_runs", "运行对比：compare-runs 命令与删除审计"),
]


@pytest.fixture
def uninstalled(monkeypatch: pytest.MonkeyPatch) -> None:
    """强制"插件未安装"。"""
    monkeypatch.setattr(document_facade, "_plugin_dir", lambda: None)


@pytest.mark.parametrize("name,purpose", _PILLAR_ONE_ENTRIES)
def test_pillar_one_entry_does_not_require_plugin(
    uninstalled: None, name: str, purpose: str
) -> None:
    """支柱①入口在未装插件时必须能拿到实现（不抛"插件未安装"）。"""
    entry = getattr(document_facade, name)
    try:
        result = entry()
    except RuntimeError as exc:
        if "插件" in str(exc):
            pytest.fail(
                f"{name}（{purpose}）依赖了 PDF 插件——"
                "它是支柱①能力，不该被可选插件绑架。"
                f"实际错误: {exc}"
            )
        raise
    except TypeError:
        # 需要参数的入口（如 compare_runs）只验证"不因插件缺失而失败"
        pytest.skip(f"{name} 需要参数，此处只验证无插件时不被拦截")
    assert result is not None


def test_table_io_matches_host_implementation(uninstalled: None) -> None:
    """`table_io()` 必须返回宿主实现，而不是插件模块。"""
    table_io = document_facade.table_io()
    assert inspect.ismodule(table_io), f"返回类型异常: {type(table_io)}"
    assert table_io.__name__ == "omnicrawler.services.table_io", (
        f"table_io() 返回的不是宿主实现，而是 {table_io.__name__}"
    )


def test_compare_runs_uses_host_review_module(uninstalled: None) -> None:
    """`compare_runs` 必须来自宿主的 `review/run_compare.py`。"""
    # 用 inspect 看源码里的导入目标，不真的调用（调用需要 StateStore）
    source = inspect.getsource(document_facade.compare_runs)
    assert "review.run_compare" in source, (
        "compare_runs 没有指向宿主 review/run_compare.py —— "
        "它被转发给插件了（2026-11-22 修过一次同类问题）"
    )
    assert "require_document" not in source, (
        "compare_runs 仍经 require_document 走插件"
    )


@pytest.mark.parametrize(
    "name",
    [
        "pdf_views",
        "pdf_region_selector",
        "document_block_class",
        "create_project_config",
        "run_pdf_extraction",
        "run_pdf_processing",
        "apply_pdf_review",
        "parse_document",
    ],
)
def test_pdf_workbench_entries_do_require_plugin(
    uninstalled: None, name: str
) -> None:
    """PDF 工作台自身的能力：未装插件时必须**明确拒绝**（不是静默返回 None）。"""
    entry = getattr(document_facade, name)
    with pytest.raises(RuntimeError) as excinfo:
        entry()
    assert "pdf-workbench" in str(excinfo.value)
