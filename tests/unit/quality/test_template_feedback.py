"""P2 模板拒绝理由诊断快照测试（PRD §3.2：无快照不入库）。"""
from __future__ import annotations

import json

from omnicrawl.quality.template_feedback import (
    REJECT_LABELS,
    TemplateFeedbackStore,
    TemplateRejectionSnapshot,
)


def _snapshot(**overrides):
    data = dict(
        url="https://example.org/news",
        domain="example.org",
        category="L2: 映射命中 news 模板",
        confidence=0.95,
        hit_source="L2",
        template_id="news/article",
        template_fields=("标题", "日期"),
        action="template_rejection",
        reject_label="字段太少",
        field_count=2,
    )
    data.update(overrides)
    return TemplateRejectionSnapshot(**data)


def test_record_persists_jsonl(tmp_path):
    """完整快照落盘为 JSONL 一行，可读回。"""
    store = TemplateFeedbackStore(tmp_path / "template_feedback.jsonl")
    assert store.record(_snapshot()) is True

    records = list(store.iter_records())
    assert len(records) == 1
    row = records[0]
    assert row["domain"] == "example.org"
    assert row["template_id"] == "news/article"
    assert row["reject_label"] == "字段太少"
    assert row["action"] == "template_rejection"
    assert row["template_fields"] == ["标题", "日期"]


def test_record_requires_snapshot_fields(tmp_path):
    """缺必需字段（domain/template_id/action）→ 无快照不入库（PRD §3.2）。"""
    store = TemplateFeedbackStore(tmp_path / "template_feedback.jsonl")
    assert store.record(_snapshot(domain="")) is False
    assert store.record(_snapshot(template_id="")) is False
    assert store.record(_snapshot(action="")) is False

    records = list(store.iter_records())
    assert records == []
    assert not (tmp_path / "template_feedback.jsonl").exists() or store.path.read_text(encoding="utf-8") == ""


def test_multiple_records_append(tmp_path):
    """多次记录追加不覆盖；损坏行被跳过。"""
    store = TemplateFeedbackStore(tmp_path / "template_feedback.jsonl")
    store.record(_snapshot(reject_label="网址不匹配"))
    store.record(_snapshot(reject_label="结构过时"))
    with store.path.open("a", encoding="utf-8") as fh:
        fh.write("{broken json}\n")

    rows = list(store.iter_records())
    assert [row["reject_label"] for row in rows] == ["网址不匹配", "结构过时"]


def test_preset_labels_available():
    """预设标签与 PRD §3.2 一致（网址不匹配/字段太少/结构过时）。"""
    assert REJECT_LABELS == ("网址不匹配", "字段太少", "结构过时")


def test_default_path_under_workspace_logs(tmp_path, monkeypatch):
    """默认路径为 workspace/logs/template_feedback.jsonl（相对 cwd）。"""
    monkeypatch.chdir(tmp_path)
    store = TemplateFeedbackStore()
    assert store.path == tmp_path / "workspace" / "logs" / "template_feedback.jsonl"
    assert store.record(_snapshot()) is True
    assert (tmp_path / "workspace" / "logs" / "template_feedback.jsonl").exists()


def test_record_payload_is_valid_json_lines(tmp_path):
    """每行都是独立可解析的 JSON。"""
    store = TemplateFeedbackStore(tmp_path / "template_feedback.jsonl")
    store.record(_snapshot(reject_label="字段太少"))
    lines = store.path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["confidence"] == 0.95
