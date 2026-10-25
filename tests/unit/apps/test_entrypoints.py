"""C-#16：``apps`` 入口参数解析、退出码与错误处理直接冒烟单测（不触碰 pdfx 内部实现）。"""
from __future__ import annotations

import argparse

import pytest

from omnicrawler.apps import _pdf_cli, field_extractor, pdf_processor


def test_field_extractor_parser_exposes_all_subcommands() -> None:
    parser = field_extractor.build_parser()
    args = parser.parse_args(["run", "--limit", "3", "--workers", "2", "--skip-ocr", "--no-auto-prepare"])
    assert args.command == "run" and args.limit == 3 and args.workers == 2
    assert args.skip_ocr is True and args.no_auto_prepare is True

    extract = parser.parse_args(["extract-only", "--limit", "1", "--workers", "1"])
    assert extract.command == "extract-only"

    review = parser.parse_args(["apply-review", "--file", "review.yaml"])
    assert review.command == "apply-review" and review.file == "review.yaml"

    fields = parser.parse_args(["fields", "--json"])
    assert fields.command == "fields" and fields.json is True


def test_pdf_processor_parser_exposes_all_subcommands() -> None:
    parser = pdf_processor.build_parser()
    init = parser.parse_args([
        "init", "--template", "t", "--output-config", "c.yaml",
        "--input", "in", "--work", "w", "--output", "o", "--ocr", "paddle",
    ])
    assert init.command == "init" and init.ocr == "paddle"

    reset = parser.parse_args(["reset", "parse"])
    assert reset.command == "reset" and reset.stage == "parse"

    run = parser.parse_args(["run", "--limit", "2", "--skip-ocr"])
    assert run.command == "run" and run.limit == 2 and run.skip_ocr is True


def test_emit_prints_indented_json(capsys) -> None:
    _pdf_cli.emit({"ok": True, "n": 2})
    out = capsys.readouterr().out
    assert '"ok": true' in out and '"n": 2' in out


def test_report_error_returns_exit_code_one_and_stderr(capsys) -> None:
    code = _pdf_cli.report_error(FileNotFoundError("missing.yaml"))
    assert code == 1
    err = capsys.readouterr().err
    assert "FileNotFoundError" in err and "missing.yaml" in err


def test_field_extractor_main_returns_one_on_load_failure(tmp_path) -> None:
    code = field_extractor.main(["--config", str(tmp_path / "nonexistent.yaml"), "status"])
    assert code == 1


def test_pdf_processor_main_returns_one_on_load_failure(tmp_path) -> None:
    code = pdf_processor.main(["--config", str(tmp_path / "nonexistent.yaml"), "status"])
    assert code == 1


def test_field_extractor_main_keyboard_interrupt_returns_130(monkeypatch: pytest.MonkeyPatch) -> None:
    import omnicrawler.pdfx.config as pdf_config

    def _raise_interrupt(_config: object) -> object:
        raise KeyboardInterrupt

    monkeypatch.setattr(pdf_config, "load_config", _raise_interrupt)
    assert field_extractor.main(["status"]) == 130


def test_pdf_processor_main_keyboard_interrupt_returns_130(monkeypatch: pytest.MonkeyPatch) -> None:
    import omnicrawler.pdfx.config as pdf_config

    def _raise_interrupt(_config: object) -> object:
        raise KeyboardInterrupt

    monkeypatch.setattr(pdf_config, "load_config", _raise_interrupt)
    assert pdf_processor.main(["status"]) == 130


def test_unknown_subcommand_exits_two() -> None:
    with pytest.raises(SystemExit) as excinfo:
        field_extractor.build_parser().parse_args(["bogus"])
    assert excinfo.value.code == 2
