"""N1：基因增强单元测试 — 缺失字段补提 + 反馈进化 + 性能上限。

覆盖：默认关闭、字段级去重、单/多节点取值、命中/未命中反馈、冷启动无基因、
MAX_AUGMENT_FIELDS_PER_PAGE 硬上限。
"""

from __future__ import annotations

from types import SimpleNamespace

from omnicrawl.quality.gene_augment import MAX_AUGMENT_FIELDS_PER_PAGE, gene_augment_html
from omnicrawl.state.scene_store import SceneStore


def _html(body: str):
    """构造最小 FetchResult 兼容对象（decode_body 只读 body + content-type）。"""
    return SimpleNamespace(
        body=body.encode("utf-8"),
        headers={"content-type": "text/html; charset=utf-8"},
    )


def _rec(data: dict):
    return SimpleNamespace(data=data)


def _seed_gene(db, scene: str, slot: str, selector: str, selector_type: str = "css") -> None:
    with SceneStore(db) as store:
        store.upsert_gene(scene, slot, selector, selector_type=selector_type)


def _fields(*names: str) -> dict:
    return {name: {"selector": "h1"} for name in names}


# ── 默认关闭（零行为）──────────────────────────────────

def test_inactive_without_scene(tmp_path) -> None:
    db = tmp_path / "scene.sqlite3"
    with SceneStore(db):
        pass
    stats = gene_augment_html(_html("<p>x</p>"), [_rec({"t": ""})], _fields("t"), "", db)
    assert stats == {"active": False, "augmented": 0, "hit": 0, "miss": 0, "skipped_no_gene": 0}


def test_inactive_db_missing(tmp_path) -> None:
    stats = gene_augment_html(_html("<p>x</p>"), [_rec({"t": ""})], _fields("t"), "scene", tmp_path / "nope.sqlite3")
    assert stats["active"] is False


def test_no_missing_fields(tmp_path) -> None:
    db = tmp_path / "scene.sqlite3"
    with SceneStore(db):
        pass
    stats = gene_augment_html(_html("<h1>y</h1>"), [_rec({"t": "已有值"})], _fields("t"), "scene", db)
    assert stats["active"] is True
    assert stats["augmented"] == 0  # 无缺失字段不激活补提


# ── 命中 / 未命中 / 冷启动 ─────────────────────────────

def test_css_single_node_hit(tmp_path) -> None:
    db = tmp_path / "scene.sqlite3"
    _seed_gene(db, "scene", "title", ".title")
    result = _html('<html><body><h1 class="title">Foo</h1></body></html>')
    record = _rec({"title": ""})
    stats = gene_augment_html(result, [record], _fields("title"), "scene", db)
    assert stats["hit"] == 1
    assert stats["augmented"] == 1
    assert record.data["title"] == "Foo"  # 补提值写回


def test_css_multi_node_list(tmp_path) -> None:
    db = tmp_path / "scene.sqlite3"
    _seed_gene(db, "scene", "tags", ".item")
    result = _html('<html><body><span class="item">a</span><span class="item">b</span></body></html>')
    record = _rec({"tags": []})
    stats = gene_augment_html(result, [record], _fields("tags"), "scene", db)
    assert stats["hit"] == 1
    assert record.data["tags"] == ["a", "b"]  # 多节点 → list


def test_gene_miss_records_feedback(tmp_path) -> None:
    db = tmp_path / "scene.sqlite3"
    _seed_gene(db, "scene", "title", ".nope")  # 选择器不命中
    result = _html('<html><body><h1>X</h1></body></html>')
    record = _rec({"title": ""})
    stats = gene_augment_html(result, [record], _fields("title"), "scene", db)
    assert stats["miss"] == 1
    assert stats["augmented"] == 0
    assert record.data["title"] == ""  # 未命中不写回
    # 反馈落库：misses 已 +1
    with SceneStore(db) as store:
        row = store.top_genes("scene", "title", limit=1)[0]
        assert row["misses"] == 1
        assert row["hits"] == 0


def test_no_gene_skipped(tmp_path) -> None:
    db = tmp_path / "scene.sqlite3"
    with SceneStore(db):
        pass
    stats = gene_augment_html(_html("<h1>x</h1>"), [_rec({"t": ""})], _fields("t"), "scene", db)
    assert stats["skipped_no_gene"] == 1
    assert stats["augmented"] == 0


# ── 性能：字段级去重 + 硬上限 ──────────────────────────

def test_field_level_dedupe(tmp_path) -> None:
    """100 条记录 × 2 缺失字段 → 每字段只补一次（非 200 次）。"""
    db = tmp_path / "scene.sqlite3"
    _seed_gene(db, "scene", "title", ".title")
    _seed_gene(db, "scene", "date", ".date")
    result = _html(
        '<html><body><h1 class="title">T</h1>'
        '<span class="date">2026</span></body></html>'
    )
    records = [_rec({"title": "", "date": ""}) for _ in range(100)]
    stats = gene_augment_html(result, records, _fields("title", "date"), "scene", db)
    assert stats["augmented"] == 2  # 字段去重：2 个字段各补一次
    assert stats["hit"] == 2
    assert records[0].data["title"] == "T"
    assert records[0].data["date"] == "2026"
    # 后续记录不填（样本反馈而非全量补全）
    assert records[1].data["title"] == ""


def test_max_fields_cap(tmp_path) -> None:
    """超过 MAX_AUGMENT_FIELDS_PER_PAGE 的缺失字段被截断。"""
    db = tmp_path / "scene.sqlite3"
    result = _html('<html><body><h1 class="t">v</h1></body></html>')
    fields: dict = {}
    for i in range(MAX_AUGMENT_FIELDS_PER_PAGE + 5):
        name = f"f{i:02d}"
        fields[name] = {"selector": "h1"}
        _seed_gene(db, "scene", name, ".t")
    records = [_rec({name: "" for name in fields})]
    stats = gene_augment_html(result, records, fields, "scene", db)
    assert stats["hit"] <= MAX_AUGMENT_FIELDS_PER_PAGE
    assert stats["augmented"] == MAX_AUGMENT_FIELDS_PER_PAGE  # cap 截断


# ── 异常韧性 ────────────────────────────────────────────

def test_malformed_html_failsafe(tmp_path) -> None:
    """畸形 HTML 不崩，返回零统计。"""
    db = tmp_path / "scene.sqlite3"
    _seed_gene(db, "scene", "title", ".title")
    stats = gene_augment_html(_html("<html><body>"), [_rec({"title": ""})], _fields("title"), "scene", db)
    assert stats["augmented"] == 0


def test_corrupt_db_failsafe(tmp_path) -> None:
    """损坏的 scene.sqlite3 不崩，静默降级。"""
    db = tmp_path / "scene.sqlite3"
    db.write_bytes(b"not a sqlite db")
    stats = gene_augment_html(_html("<h1>x</h1>"), [_rec({"t": ""})], _fields("t"), "scene", db)
    assert stats["augmented"] == 0
