"""Native PDF extraction must refresh semantic changes while preserving human decisions."""
from __future__ import annotations

import csv
import os

import pytest
import yaml

from omnicrawler.pdfx.config import load_config
from omnicrawler.pdfx.database import Database
from omnicrawler.pdfx.review import apply_review
from omnicrawler.pdfx.service import run_extraction

canvas = pytest.importorskip("reportlab.pdfgen.canvas")


def setup_project(tmp_path, *, wrong_rule=False, count=1):
    source = tmp_path / "in"
    source.mkdir()
    for index in range(count):
        c = canvas.Canvas(str(source / f"sample{index}.pdf"))
        c.drawString(72, 760, f"Invoice: A{index} Amount: 100 Company: Alias")
        c.showPage()
        c.save()
    data = {
        "project_name": "dependency-proof", "input_dir": str(source),
        "work_dir": str(tmp_path / "work"), "output_dir": str(tmp_path / "out"),
        "database": str(tmp_path / "work" / "state.sqlite3"),
        "parser": {"workers": 1, "min_native_chars": 5}, "ocr": {"backend": "none"},
        "retrieval": {"top_pages": 1, "fallback_pages": [1]},
        "llm": {"provider": "disabled"}, "extraction": {"workers": 1},
        "fields": [{"name": "invoice", "label": "Unknown" if wrong_rule else "Invoice", "type": "code",
                    "source": "content", "patterns": [r"Unknown: (?P<value>[A-Z0-9]+)" if wrong_rule else r"Invoice: (?P<value>[A-Z0-9]+)"]}],
    }
    path = tmp_path / "project.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path, data


def write_config(path, data):
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def amount_field():
    return {"name": "amount", "label": "Amount", "type": "number", "source": "content",
            "patterns": [r"Amount: (?P<value>[0-9]+)"]}


def test_added_field_reextracts_finished_native_documents_without_reparsing(tmp_path):
    path, data = setup_project(tmp_path)
    assert run_extraction(path)["extract"]["records"] == 1
    assert run_extraction(path)["extract"]["selected"] == 0
    data["fields"].append(amount_field())
    write_config(path, data)
    refreshed = run_extraction(path)
    assert refreshed["processing"]["parse"]["parsed"] == 0
    assert refreshed["extract"]["records"] == 1
    with Database(load_config(path).database) as db:
        assert db.fetchone("SELECT normalized_value FROM field_values WHERE field_name='amount'")[0] == "100"
    assert run_extraction(path)["extract"]["selected"] == 0


def test_corrected_rule_recovers_a_previously_empty_document(tmp_path):
    path, data = setup_project(tmp_path, wrong_rule=True)
    assert run_extraction(path)["extract"]["records"] == 0
    data["fields"][0]["patterns"] = [r"Invoice: (?P<value>[A-Z0-9]+)"]
    write_config(path, data)
    assert run_extraction(path)["extract"]["records"] == 1


@pytest.mark.parametrize("decision", ["accept", "reject"])
def test_changed_dependencies_preserve_reviewed_records_and_emit_warning(tmp_path, decision):
    path, data = setup_project(tmp_path)
    run_extraction(path)
    config = load_config(path)
    review = tmp_path / "review.csv"
    with Database(config.database) as db:
        record = db.fetchone("SELECT record_id FROM records")[0]
        with review.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["记录ID", "复核决定", "Invoice"])
            writer.writeheader()
            writer.writerow({"记录ID": record, "复核决定": decision, "Invoice": "MANUAL"})
        apply_review(config, db, review)
    data["fields"].append(amount_field())
    write_config(path, data)
    events = []
    result = run_extraction(path, callback=lambda stage, payload: events.append((stage, payload)))
    assert result["extract"]["selected"] == 0
    assert result["extract"]["reviewed_dependency_changes"] == 1
    assert any(stage == "warnings" and any("新规则未自动应用" in str(item) for item in payload["items"])
               for stage, payload in events)
    with Database(config.database) as db:
        assert db.fetchone("SELECT review_status FROM records")[0] == ("human_accepted" if decision == "accept" else "human_rejected")
        assert db.fetchone("SELECT COUNT(*) FROM field_values WHERE field_name='amount'")[0] == 0
        expected = "MANUAL" if decision == "accept" else "A0"
        assert db.fetchone("SELECT normalized_value FROM field_values WHERE field_name='invoice'")[0] == expected


def test_limited_refresh_keeps_unprocessed_documents_dirty(tmp_path):
    path, data = setup_project(tmp_path, count=2)
    assert run_extraction(path)["extract"]["records"] == 2
    data["fields"].append(amount_field())
    write_config(path, data)
    assert run_extraction(path, limit=1)["extract"]["records"] == 1
    assert run_extraction(path)["extract"]["records"] == 1
    assert run_extraction(path)["extract"]["selected"] == 0


def test_entity_csv_same_size_same_timestamp_reloads_and_reextracts(tmp_path):
    path, data = setup_project(tmp_path)
    master = tmp_path / "entities.csv"
    master.write_text("standard_name,aliases\nFirst,Alias\n", encoding="utf-8")
    data["normalization"] = {"entity_master_csv": str(master)}
    data["fields"] = [{"name": "company", "label": "Company", "type": "entity", "source": "content",
                      "patterns": [r"Company: (?P<value>[A-Za-z]+)"]}]
    write_config(path, data)
    run_extraction(path)
    config = load_config(path)
    with Database(config.database) as db:
        assert db.fetchone("SELECT normalized_value FROM field_values WHERE field_name='company'")[0] == "First"
    previous = master.stat()
    master.write_text("standard_name,aliases\nOther,Alias\n", encoding="utf-8")
    os.utime(master, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    assert master.stat().st_size == previous.st_size
    assert run_extraction(path)["extract"]["records"] == 1
    with Database(config.database) as db:
        assert db.fetchone("SELECT normalized_value FROM field_values WHERE field_name='company'")[0] == "Other"


def test_review_arriving_during_extraction_is_preserved_without_failure(tmp_path, monkeypatch):
    from omnicrawler.pdfx import extraction

    path, data = setup_project(tmp_path)
    run_extraction(path)
    prior_config = load_config(path)
    with Database(prior_config.database) as db:
        record = db.fetchone("SELECT record_id FROM records")[0]
    review = tmp_path / "racing-review.csv"
    with review.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["记录ID", "复核决定", "Invoice"])
        writer.writeheader()
        writer.writerow({"记录ID": record, "复核决定": "accept", "Invoice": "MANUAL"})
    original = extraction.rule_extract
    def rule_extract(*args):
        values = original(*args)
        with Database(prior_config.database) as reviewer:
            apply_review(prior_config, reviewer, review)
        return values
    monkeypatch.setattr(extraction, "rule_extract", rule_extract)
    data["fields"].append(amount_field())
    write_config(path, data)
    result = run_extraction(path)["extract"]
    assert result["failed"] == 0 and result["records"] == 0
    assert result["reviewed_dependency_changes"] == 1
    with Database(prior_config.database) as db:
        assert db.fetchone("SELECT review_status FROM records")[0] == "human_accepted"
        assert db.fetchone("SELECT normalized_value FROM field_values WHERE field_name='invoice'")[0] == "MANUAL"
