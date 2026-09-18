"""走查 R1.1：近似重复去重的「字段选择」必须真的运行，且「没运行」必须可见。

背景（0.13.0 实测，2026-09-18 走查 R1.1）：`near_duplicate_fields` 的默认值是英文硬编码
``["title", "text"]``，而本项目分析器产出的字段名是**中文**（标题 / 正文 / 内容_p …）
⇒ 拼出的比较文本恒为空 ⇒ ``continue`` ⇒ **一条记录都不会进入 simhash** ⇒
``near_duplicates`` 恒为 0，而质量报告照样给 ``average_quality_score: 1.0``。

实测场景：某电商站点交付 448 条、实际只有 80 个不同商品（同一商品最多重复 16 次），
报告却显示零重复。即**不是「没发现重复」，而是判据从未被调用** —— 度量失败被读成了业务结论。

本文件同时把「旧默认会静默跳过全部记录」这一事实固定成用例，防止有人把默认值改回去。
"""

from __future__ import annotations

from pathlib import Path

from omnicrawler.core.config import AppConfig
from omnicrawler.core.models import CrawlRequest, ExtractedRecord
from omnicrawler.quality.data_intelligence import _effective_dedup_fields, enrich_records
from omnicrawler.quality.quality_report import build_quality_report
from omnicrawler.state import StateStore

_URL = "https://shop.example.com/list"
_ROW = {"标题": "Squirtle", "价格": "£63.00", "库存": "In stock"}


def _config(root: Path, **data_quality: object) -> AppConfig:
    return AppConfig(
        root / "c.yaml",
        root,
        {
            "project": {"name": "x", "workspace": str(root / "work")},
            "data_quality": dict(data_quality),
        },
        root / "work",
    )


def _records(count: int, *, distinct: int = 1) -> list[ExtractedRecord]:
    """造 count 条记录，其中只有 distinct 种不同内容。"""
    return [
        ExtractedRecord(
            _URL,
            "item",
            {**_ROW, "标题": f"Squirtle-{index % distinct}"},
        )
        for index in range(count)
    ]


class TestEffectiveDedupFields:
    def test_未配置时用中文文本字段而非英文硬编码(self, tmp_path: Path) -> None:
        picked = _effective_dedup_fields(_records(5), [])
        assert "标题" in picked, "中文字段名必须能成为比较字段"
        assert "价格" in picked
        assert "title" not in picked

    def test_显式配置优先(self, tmp_path: Path) -> None:
        assert _effective_dedup_fields(_records(5), ["标题"]) == ["标题"]

    def test_框架列不参与比较(self, tmp_path: Path) -> None:
        record = ExtractedRecord(
            _URL,
            "item",
            {"record_id": "abc", "source_url": _URL, "record_type": "item",
             "created_at": "2026-01-01", "标题": "甲"},
        )
        assert _effective_dedup_fields([record], []) == ["标题"]

    def test_全空文本时返回空列表(self) -> None:
        record = ExtractedRecord(_URL, "item", {"标题": "", "备注": "   "})
        assert _effective_dedup_fields([record], []) == []


class TestEnrichRecordsDedupVisibility:
    def test_出厂默认必须是自动挑字段(self) -> None:
        """★ 反向断言的落点之一：**默认值本身**也要被守卫住。

        只测「代码路径」不够 —— 若把 DEFAULTS 改回英文硬编码，未显式配置的调用方
        （即绝大多数真实任务）会静默退回旧行为，而代码路径的用例仍然是绿的。
        """
        from omnicrawler.core.config import DEFAULTS

        assert DEFAULTS["data_quality"]["near_duplicate_fields"] == [], (
            "默认不得是英文硬编码字段名：中文配置下它会取不到任何值，"
            "让去重判据静默失效（near_duplicates 恒 0 而质量分仍报 1.0）"
        )

    def test_默认值喂进去也必须能发现重复(self, tmp_path: Path) -> None:
        """与上一条配对：即使有人改回英文默认，这条会立刻变红。"""
        from omnicrawler.core.config import DEFAULTS

        default_fields = list(DEFAULTS["data_quality"]["near_duplicate_fields"])
        records = _records(100, distinct=10)
        summary = enrich_records(
            records, _config(tmp_path, near_duplicate_fields=default_fields)
        )
        assert summary["dedup_compared"] == 100
        assert summary["near_duplicates"] >= 80

    def test_中文配置下重复能被发现且回报比对条数(self, tmp_path: Path) -> None:
        root = tmp_path
        records = _records(100, distinct=10)  # 100 条 / 10 种内容 ⇒ 90 条重复
        summary = enrich_records(records, _config(root))

        assert summary["dedup_compared"] == 100, "每条记录都应参与比较"
        assert summary["near_duplicates"] >= 80, "同内容的记录必须被判为近似重复"
        assert "标题" in summary["dedup_fields"]

    def test_旧英文默认会静默跳过全部记录(self, tmp_path: Path) -> None:
        """★ 反向护栏：把默认值改回 ["title","text"] 时，本用例描述的现象就会回来。

        此时 near_duplicates 为 0 **不是因为数据干净**，而是因为判据没有可比较的字段。
        """
        records = _records(100, distinct=10)
        summary = enrich_records(
            records, _config(tmp_path, near_duplicate_fields=["title", "text"])
        )
        assert summary["dedup_compared"] == 0
        assert summary["near_duplicates"] == 0, (
            "旧默认下即使 90% 是重复，也会报零重复 —— 这正是被误读成「数据干净」的原因"
        )

    def test_比对条数不足时仍如实回报(self, tmp_path: Path) -> None:
        records = _records(5)
        records[0].data["标题"] = ""
        records[0].data["价格"] = ""
        records[0].data["库存"] = ""
        summary = enrich_records(records, _config(tmp_path))
        assert summary["dedup_compared"] == 4
        assert summary["dedup_skipped"] == 1


class TestQualityReportExposesDedupState:
    def _report(self, tmp_path: Path, **data_quality: object) -> dict:
        records = _records(100, distinct=10)
        config = _config(tmp_path, **data_quality)
        enrich_records(records, config)
        with StateStore(tmp_path / "state.sqlite3") as state:
            run_id = state.start_run("x", "c.yaml")
            state.save_records(run_id, CrawlRequest(_URL), records)
            return build_quality_report(config, state, run_id)

    def test_默认配置下报告回报比对条数且无告警(self, tmp_path: Path) -> None:
        report = self._report(tmp_path)
        assert report["dedup_compared"] == 100
        assert report["near_duplicates"] >= 80
        assert report["dedup_notice"] == ""

    def test_判据未运行时必须给出可见告警(self, tmp_path: Path) -> None:
        """★ 核心护栏：0 比对 + 有记录 ⇒ 必须显式说明「0 不代表没有重复」。"""
        report = self._report(tmp_path, near_duplicate_fields=["title", "text"])
        assert report["dedup_compared"] == 0
        assert report["near_duplicates"] == 0
        assert report["dedup_notice"], "判据没被调用时不得静默通过"
        assert "不代表" in report["dedup_notice"]
