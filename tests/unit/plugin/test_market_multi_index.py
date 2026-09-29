"""多索引聚合（#77 Phase 1）：逐源验签、去重与优先级、信任分级、失败可见。

判据取向（与单源路径同一口径）：
- **fail-closed**：源的信任根为空 ⇒ 该源判失败，绝不"不验签就用"；
- **失败可见**：取数/验签失败的源必须出现在报告里带 error，不静默丢弃；
- **不静默择一**：同一 id 出现不同创作者指纹 ⇒ 双方标冲突交用户判断。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from omnicrawler.plugins import signing
from omnicrawler.plugins.market_client import aggregate_catalogs, trust_level


def _write_signed_catalog(
    root: Path,
    private_key: bytes,
    *,
    plugins: tuple[dict[str, object], ...] = (),
    templates: tuple[dict[str, object], ...] = (),
    sequence: int = 1,
    tamper: bool = False,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    catalog = {
        "schema_version": 1,
        "sequence": sequence,
        "generated_at": f"2026-09-29T00:00:{sequence:02d}+00:00",
        "plugins": list(plugins),
        "templates": list(templates),
    }
    raw = (json.dumps(catalog, sort_keys=True) + "\n").encode()
    (root / "catalog.json").write_bytes(raw)
    (root / "catalog.json.sig").write_bytes(signing.sign_bytes(raw, private_key))
    if tamper:
        (root / "catalog.json").write_bytes(raw.replace(b'"sequence": 1', b'"sequence": 9', 1))


def _entry(plugin_id: str, *, fingerprint: str = "fp-a") -> dict[str, object]:
    return {
        "id": plugin_id,
        "name": plugin_id,
        "version": "1.0.0",
        "creator_fingerprint": fingerprint,
        "plugin_file": f"plugins/{plugin_id}/versions/1.0.0/plugin.py",
    }


def test_trust_level_mapping() -> None:
    assert trust_level("curated") == "official"
    assert trust_level("community") == "community"
    assert trust_level("topic") == "community"
    assert trust_level("未知名目") == "community"  # 未知一律按"未审核"展示


def test_single_source_path_still_works(tmp_path: Path) -> None:
    """只有一个源时行为与单源一致（官方策展默认不变）。"""
    private_key, public_key = signing.generate_keypair()
    market = tmp_path / "market"
    _write_signed_catalog(market, private_key, plugins=(_entry("demo"),))

    result = aggregate_catalogs(
        [{"url": str(market), "trust": public_key.decode(), "kind": "curated", "priority": 0,
          "enabled": True}],
        cache_root=tmp_path / "cache",
    )

    assert [p["id"] for p in result["plugins"]] == ["demo"]
    assert result["plugins"][0]["_trust"] == "official"
    assert result["plugins"][0]["_source"] == str(market)
    assert result["sources"] == [
        {"url": str(market), "kind": "curated", "trust": "official", "ok": True, "error": "", "count": 1}
    ]
    assert result["conflicts"] == []


def test_two_sources_merge_with_labels_and_priority(tmp_path: Path) -> None:
    """多源合并 + 信任分级 + 优先级排序（小的在前）。"""
    key_a, pub_a = signing.generate_keypair()
    key_b, pub_b = signing.generate_keypair()
    official = tmp_path / "official"
    community = tmp_path / "community"
    _write_signed_catalog(official, key_a, plugins=(_entry("from-official"),))
    _write_signed_catalog(community, key_b, plugins=(_entry("from-community"),))

    result = aggregate_catalogs(
        [
            {"url": str(community), "trust": pub_b.decode(), "kind": "community",
             "priority": 50, "enabled": True},
            {"url": str(official), "trust": pub_a.decode(), "kind": "curated",
             "priority": 0, "enabled": True},
        ],
        cache_root=tmp_path / "cache",
    )

    assert [p["id"] for p in result["plugins"]] == ["from-official", "from-community"], (
        "优先级小的排在前"
    )
    by_id = {p["id"]: p for p in result["plugins"]}
    assert by_id["from-official"]["_trust"] == "official"
    assert by_id["from-community"]["_trust"] == "community"
    assert all(s["ok"] for s in result["sources"])


def test_failed_source_is_visible_and_others_survive(tmp_path: Path) -> None:
    """★ 一个源烂掉不能拖垮其余源，且失败必须可见（带 error，不静默丢弃）。"""
    key_a, pub_a = signing.generate_keypair()
    key_b, _pub_b = signing.generate_keypair()
    good = tmp_path / "good"
    bad = tmp_path / "bad"
    _write_signed_catalog(good, key_a, plugins=(_entry("good-one"),))
    _write_signed_catalog(bad, key_b, plugins=(_entry("tampered-one"),), tamper=True)

    result = aggregate_catalogs(
        [
            {"url": str(good), "trust": pub_a.decode(), "kind": "curated", "priority": 0, "enabled": True},
            {"url": str(bad), "trust": pub_a.decode(), "kind": "community", "priority": 10, "enabled": True},
        ],
        cache_root=tmp_path / "cache",
    )

    assert [p["id"] for p in result["plugins"]] == ["good-one"]
    bad_report = next(s for s in result["sources"] if s["url"] == str(bad))
    assert bad_report["ok"] is False and bad_report["error"], "失败源必须带 error 出现在报告里"
    assert bad_report["count"] == 0


def test_source_without_trust_root_is_refused(tmp_path: Path) -> None:
    """★ fail-closed：没有信任根可用时该源判失败，而不是"不验签就采用"。"""
    key_a, _pub_a = signing.generate_keypair()
    market = tmp_path / "market"
    _write_signed_catalog(market, key_a, plugins=(_entry("demo"),))

    result = aggregate_catalogs(
        [{"url": str(market), "trust": "", "kind": "community", "priority": 0, "enabled": True}],
        official_trust_source="",
        cache_root=tmp_path / "cache",
    )

    assert result["plugins"] == []
    assert result["sources"][0]["ok"] is False
    assert "fail-closed" in result["sources"][0]["error"]


def test_disabled_source_is_reported_not_silently_skipped(tmp_path: Path) -> None:
    key_a, pub_a = signing.generate_keypair()
    market = tmp_path / "market"
    _write_signed_catalog(market, key_a, plugins=(_entry("demo"),))

    result = aggregate_catalogs(
        [{"url": str(market), "trust": pub_a.decode(), "kind": "curated", "priority": 0, "enabled": False}],
        cache_root=tmp_path / "cache",
    )

    assert result["plugins"] == []
    assert result["sources"][0]["ok"] is False
    assert result["sources"][0]["error"] == "已禁用"


def test_same_id_and_fingerprint_dedups_but_keeps_all_sources(tmp_path: Path) -> None:
    key_a, pub_a = signing.generate_keypair()
    key_b, pub_b = signing.generate_keypair()
    mirror_a = tmp_path / "a"
    mirror_b = tmp_path / "b"
    _write_signed_catalog(mirror_a, key_a, plugins=(_entry("shared", fingerprint="fp-1"),))
    _write_signed_catalog(mirror_b, key_b, plugins=(_entry("shared", fingerprint="fp-1"),))

    result = aggregate_catalogs(
        [
            {"url": str(mirror_a), "trust": pub_a.decode(), "kind": "curated", "priority": 0, "enabled": True},
            {"url": str(mirror_b), "trust": pub_b.decode(), "kind": "community", "priority": 5, "enabled": True},
        ],
        cache_root=tmp_path / "cache",
    )

    assert len(result["plugins"]) == 1, "同 id + 同指纹 ⇒ 去重成一条"
    entry = result["plugins"][0]
    assert entry["_source"] == str(mirror_a), "取优先级更高的源作为主来源"
    assert set(entry["_sources"]) == {str(mirror_a), str(mirror_b)}, "但保留全部来源"
    assert entry["_conflict"] is False


def test_same_id_different_fingerprint_flagged_as_conflict(tmp_path: Path) -> None:
    """★ 同一 id 出现不同创作者指纹 ⇒ 双方标冲突，不静默择一（防换密钥接管同名 id）。"""
    key_a, pub_a = signing.generate_keypair()
    key_b, pub_b = signing.generate_keypair()
    official = tmp_path / "official"
    impostor = tmp_path / "impostor"
    _write_signed_catalog(official, key_a, plugins=(_entry("popular", fingerprint="fp-honest"),))
    _write_signed_catalog(impostor, key_b, plugins=(_entry("popular", fingerprint="fp-other"),))

    result = aggregate_catalogs(
        [
            {"url": str(official), "trust": pub_a.decode(), "kind": "curated", "priority": 0, "enabled": True},
            {"url": str(impostor), "trust": pub_b.decode(), "kind": "community", "priority": 5, "enabled": True},
        ],
        cache_root=tmp_path / "cache",
    )

    assert len(result["plugins"]) == 2, "不同指纹 ⇒ 两条都要留下"
    assert {p["creator_fingerprint"] for p in result["plugins"]} == {"fp-honest", "fp-other"}
    assert all(p["_conflict"] is True for p in result["plugins"])
    assert result["conflicts"] == ["popular"]


def test_templates_are_aggregated_separately(tmp_path: Path) -> None:
    key_a, pub_a = signing.generate_keypair()
    market = tmp_path / "market"
    _write_signed_catalog(
        market,
        key_a,
        plugins=(_entry("plug"),),
        templates=({"id": "tpl", "name": "tpl", "creator_fingerprint": "fp-t"},),
    )

    result = aggregate_catalogs(
        [{"url": str(market), "trust": pub_a.decode(), "kind": "curated", "priority": 0, "enabled": True}],
        cache_root=tmp_path / "cache",
    )

    assert [t["id"] for t in result["templates"]] == ["tpl"]
    assert [p["id"] for p in result["plugins"]] == ["plug"]
