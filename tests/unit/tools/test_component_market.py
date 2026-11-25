"""组件市场：与插件/模板同等成熟度。

## 三支的成熟度定义

市场下辖插件 / 模板 / 组件三支。前两支的完整链路是：

```
拉 catalog → 验签 → 浏览条目 → 展示风险与体积 → 用户确认 → 安装 → 校验/卸载
```

组件此前只有**离线 zip 导入**，没有任何市场能力。本文件钉住补齐后的能力。

## 组件的特殊之处

组件条目**不含字节**：市场只托管 recipe 声明，构件从上游 URL 拉。
所以「风险」不是包签名，而是：

* 会连哪些**上游域名**（用户要能看见）；
* 要下载**多少字节**（几百 MB 的模型必须提前知道）；
* 构件是否都带 **sha256**（缺哈希就无法校验来源 ⇒ 必须阻断）。

## 判据

1. `component_market_logic` 能从容错地解析 catalog 条目；
2. 缺 sha256 / 缺 recipe_digest ⇒ **阻断**（不只是警告）；
3. 大体积与未知上游 ⇒ **警告**（能装但要让用户知道）；
4. 面板在有 catalog 上下文时显示市场区，无上下文时不画空列表；
5. **验签失败必须停**（fail-closed），不能静默降级去读未验签的目录。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.gui.views.component_market_logic import (
    LARGE_DOWNLOAD_BYTES,
    components_from_catalog,
    describe_for_confirm,
    human_bytes,
    parse_component_entry,
    pre_install_checks,
)


def _entry(**overrides: object) -> dict:
    base = {
        "id": "demo",
        "version": "1.0.0",
        "name": "示例组件",
        "publisher": "tester",
        "recipe_file": "components/demo/recipe.json",
        "recipe_digest": "a" * 64,
        "declared_total_bytes": 1024,
        "artifacts": [
            {
                "url": "https://github.com/x/y/raw/main/z.bin",
                "sha256": "b" * 64,
                "size": 1024,
            }
        ],
    }
    base.update(overrides)
    return base


def test_parses_valid_entry_with_host_and_risk() -> None:
    """合法条目应解析出上游域名与风险等级。"""
    entry = parse_component_entry(_entry())
    assert entry is not None
    assert entry.id == "demo"
    assert entry.artifacts[0].host == "github.com"
    assert entry.artifacts[0].host_risk == "low"


def test_unknown_upstream_is_flagged_medium_or_unknown() -> None:
    """未知域名必须被判成 unknown（而非误判为可信）。"""
    entry = parse_component_entry(
        _entry(
            artifacts=[
                {"url": "https://evil.example.invalid/x.bin", "sha256": "c" * 64, "size": 1}
            ]
        )
    )
    assert entry is not None
    assert entry.artifacts[0].host_risk == "unknown"


def test_missing_sha256_blocks_install() -> None:
    """缺 sha256 必须**阻断**——没有哈希就无法校验来源。"""
    entry = parse_component_entry(
        _entry(artifacts=[{"url": "https://github.com/x", "sha256": "", "size": 1}])
    )
    assert entry is not None
    blockers, _warnings = pre_install_checks(entry)
    assert blockers, "缺 sha256 应阻断安装"
    assert any("sha256" in b for b in blockers)


def test_missing_recipe_digest_blocks_install() -> None:
    """缺 recipe_digest 必须阻断——无法核对声明是否被改动。"""
    entry = parse_component_entry(_entry(recipe_digest=""))
    assert entry is not None
    blockers, _warnings = pre_install_checks(entry)
    assert any("recipe" in b.lower() for b in blockers)


def test_large_download_warns_but_does_not_block() -> None:
    """超过阈值只**警告**——用户可能确实要装大模型。"""
    big = LARGE_DOWNLOAD_BYTES + 1
    entry = parse_component_entry(
        _entry(
            declared_total_bytes=big,
            artifacts=[{"url": "https://github.com/x", "sha256": "d" * 64, "size": big}],
        )
    )
    assert entry is not None
    blockers, warnings = pre_install_checks(entry)
    assert not blockers
    assert any("MB" in w for w in warnings)


def test_bad_entry_does_not_break_whole_list() -> None:
    """一个坏条目不该让整个列表失败（返回 None 后被过滤）。"""
    catalog = {
        "components": [
            _entry(id="good-1"),
            {"id": "", "version": "1.0.0"},  # 缺 id
            "not-a-dict",
            _entry(id="good-2"),
        ]
    }
    entries = components_from_catalog(catalog)
    assert [e.id for e in entries] == ["good-1", "good-2"]


def test_catalog_without_components_is_compatible() -> None:
    """旧 catalog（无 components）返回空列表，不抛异常。"""
    assert components_from_catalog({"plugins": [], "templates": []}) == []


def test_confirm_text_mentions_upstream_and_no_endorsement() -> None:
    """确认文案必须写明上游域名，并声明本项目不为上游背书。"""
    entry = parse_component_entry(_entry())
    assert entry is not None
    text = describe_for_confirm(entry)
    assert "github.com" in text
    assert "背书" in text, "确认文案必须说明不为上游内容背书"


def test_human_bytes_is_readable() -> None:
    assert human_bytes(0) == "未知"
    assert "KB" in human_bytes(2048) or "MB" in human_bytes(2048)


pytest.importorskip("PySide6")


def test_panel_shows_market_section_only_with_catalog() -> None:
    """有 catalog 上下文才有市场区；没有时不画空列表。"""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    assert app is not None

    from omnicrawler.gui.views.component_manager import ComponentManagerPanel

    # ★ 2026-11-24（用户第二轮反馈）：不再单列「已安装组件」，改成**一份**列表。
    # 所以「有没有 catalog」不再决定**列表是否存在**，而是决定：
    #
    #   * 有 catalog ⇒ 列表里有市场条目（可搜索、可安装）；
    #   * 无 catalog ⇒ 列表仍在（用来承载本地已装/离线导入的条目，仍可卸载），
    #     只是没有市场内容，也不画搜索框。
    without = ComponentManagerPanel(None)
    try:
        assert without._market_list is not None, "列表应始终存在（承载本地已装条目）"
        assert without._market_search is None, "无 catalog 时不该有市场搜索框"
        assert without._market_install is None, "无 catalog 时不该有市场安装按钮"
    finally:
        without.deleteLater()

    with_catalog = ComponentManagerPanel(None, catalog_url="https://example.invalid/")
    try:
        assert with_catalog._market_list is not None, "有 catalog 时应显示市场区"
        assert with_catalog._market_search is not None, "有 catalog 时应有搜索框"
        assert with_catalog._market_install is not None, "有 catalog 时应有安装按钮"
    finally:
        with_catalog.deleteLater()


def test_panel_stops_on_signature_failure() -> None:
    """验签失败必须**停**，不得静默降级去读未验签的目录。

    这里用一个本地目录，但其 catalog.json.sig 与内容不匹配。
    """
    import json

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    assert app is not None

    from omnicrawler.gui.views.component_manager import ComponentManagerPanel

    tmp = Path(__file__).resolve().parent / "_signature_failure_case"
    tmp.mkdir(exist_ok=True)
    try:
        (tmp / "catalog.json").write_text(
            json.dumps({"schema_version": 2, "plugins": [], "templates": [], "components": []}),
            encoding="utf-8",
        )
        (tmp / "catalog.json.sig").write_text("bm90LWEtc2lnbmF0dXJl", encoding="ascii")

        panel = ComponentManagerPanel(
            None,
            catalog_url=str(tmp) + "/",
            base=tmp,
            trust_source=str(tmp / "absent.pub.pem"),
        )
        try:
            panel._reload_catalog()
            # ★ 2026-11-25：`_reload_catalog` 已改为后台线程（原先同步做网络 IO，
            # 最坏卡 GUI 30 秒）。测试必须等 worker 真正结束再读状态。
            worker = panel._worker
            assert worker is not None, "`_reload_catalog` 没有启动 worker"
            assert worker.wait(15000), "worker 15 秒内没结束"
            app.processEvents()
            status = panel._market_status.text()
            assert panel._entries == [], "验签失败却解析出了条目"
            assert "验签" in status or "失败" in status or "无法" in status, (
                f"验签失败应给出明确提示，实际：{status!r}"
            )
        finally:
            panel.deleteLater()
    finally:
        for leftover in ("catalog.json", "catalog.json.sig"):
            (tmp / leftover).unlink(missing_ok=True)
        try:
            tmp.rmdir()
        except OSError:
            pass
