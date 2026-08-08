from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytest.importorskip("ruamel.yaml")

from omnicrawl.gui.core.template_loader import TemplateLoader


def _write_template(dir_path: Path, name: str, payload: dict) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / f"{name}.yaml").write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")


def _loader(tmp_path: Path) -> TemplateLoader:
    return TemplateLoader(builtin_dir=tmp_path / "builtin", user_dir=None)


def test_combine_merges_seed_urls_deduped(tmp_path: Path) -> None:
    _write_template(tmp_path / "builtin", "t1", {
        "template": {"id": "t1", "name": "T1"},
        "project": {"name": "t1"},
        "source": {"kind": "static_html", "seeds": ["https://a.example/x", "https://b.example/y"]},
    })
    _write_template(tmp_path / "builtin", "t2", {
        "template": {"id": "t2", "name": "T2"},
        "project": {"name": "t2"},
        "source": {"kind": "static_html", "seeds": ["https://b.example/y", "https://c.example/z"]},
    })

    merged = _loader(tmp_path).combine(["t1", "t2"])

    assert merged is not None
    assert merged.seed_urls == ["https://a.example/x", "https://b.example/y", "https://c.example/z"]


def test_combine_merges_fields_later_wins_on_conflict(tmp_path: Path) -> None:
    _write_template(tmp_path / "builtin", "t1", {
        "template": {"id": "t1", "name": "T1"},
        "project": {"name": "t1"},
        "source": {"kind": "static_html", "seeds": ["https://a.example/x"]},
        "extract": {"fields": {"title": {"selector": "h1"}}},
    })
    _write_template(tmp_path / "builtin", "t2", {
        "template": {"id": "t2", "name": "T2"},
        "project": {"name": "t2"},
        "source": {"kind": "static_html", "seeds": ["https://a.example/x"]},
        "extract": {
            "fields": {
                "title": {"selector": "h1.title"},
                "price": {"selector": "span.price"},
            }
        },
    })

    merged = _loader(tmp_path).combine(["t1", "t2"])

    assert merged is not None
    names = {f.name: f for f in merged.fields}
    assert set(names) == {"title", "price"}
    assert names["title"].selector == "h1.title"
    assert names["price"].selector == "span.price"


def test_combine_explicit_later_value_overrides_first(tmp_path: Path) -> None:
    _write_template(tmp_path / "builtin", "t1", {
        "template": {"id": "t1", "name": "T1"},
        "project": {"name": "t1"},
        "source": {"kind": "static_html", "seeds": ["https://a.example/x"]},
        "crawl": {"max_pages": 5, "concurrency": 2},
    })
    _write_template(tmp_path / "builtin", "t2", {
        "template": {"id": "t2", "name": "T2"},
        "project": {"name": "t2"},
        "source": {"kind": "static_html", "seeds": ["https://a.example/x"]},
        "crawl": {"max_pages": 20},
    })

    merged = _loader(tmp_path).combine(["t1", "t2"])

    assert merged is not None
    assert merged.max_pages == 20
    assert merged.concurrency == 2  # 未冲突的段保留第一个模板


def test_combine_default_values_do_not_clobber_first(tmp_path: Path) -> None:
    _write_template(tmp_path / "builtin", "t1", {
        "template": {"id": "t1", "name": "T1"},
        "project": {"name": "t1"},
        "source": {"kind": "static_html", "seeds": ["https://a.example/x"]},
        "crawl": {"max_pages": 12},
    })
    _write_template(tmp_path / "builtin", "t2", {
        "template": {"id": "t2", "name": "T2"},
        "project": {"name": "t2"},
        "source": {"kind": "static_html", "seeds": ["https://a.example/x"]},
    })

    merged = _loader(tmp_path).combine(["t1", "t2"])

    assert merged is not None
    assert merged.max_pages == 12


def test_combine_missing_template_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        _loader(tmp_path).combine(["does-not-exist"])


def test_combine_empty_names_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        _loader(tmp_path).combine([])
