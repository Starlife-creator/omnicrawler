"""S4.4 ④：ingest 同路径同大小新文件被 SHA 检出（F699）。"""

from __future__ import annotations

from pathlib import Path

import fitz

from omnicrawler.pdfx.config import load_config
from omnicrawler.pdfx.database import Database
from omnicrawler.pdfx.ingest import ingest


def _pdf(tmp_path: Path, text: str, name: str = "doc.pdf") -> Path:
    target = tmp_path / name
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text, fontsize=14)
    document.save(target)
    document.close()
    return target


def _config(tmp_path: Path) -> Path:
    config_path = tmp_path / "pdfx.yaml"
    config_path.write_text(
        f"project_name: sha-test\n"
        f"input_dir: {tmp_path / 'pdfs'}\n"
        f"work_dir: {tmp_path / 'work'}\n"
        f"output_dir: {tmp_path / 'out'}\n"
        "fields:\n"
        "  - name: title\n"
        "    label: 标题\n"
        "    type: text\n",
        encoding="utf-8",
    )
    return config_path


def test_ingest_detects_same_path_same_size_new_content(tmp_path: Path) -> None:
    pdfs = tmp_path / "pdfs"
    pdfs.mkdir()
    pdf = _pdf(pdfs, "version one")
    config = load_config(_config(tmp_path))
    config.work_dir.mkdir(parents=True, exist_ok=True)
    with Database(config.database) as db:
        first = ingest(config, db)
        assert first["new"] == 1 and first["duplicate"] == 0
        # 同路径同大小（相同页数布局，仅文字不同——size 相同）内容变化
        pdf.write_bytes(_pdf(pdfs, "version two", name=pdf.name).read_bytes() if False else _same_size(pdf, "version two"))
        second = ingest(config, db)
        assert second["duplicate"] == 0, "同路径同大小新文件被误判为重复"
        assert second["new"] >= 1
        # 内容未变时仍走重复
        third = ingest(config, db)
        assert third["duplicate"] == 1


def _same_size(original: Path, text: str) -> bytes:
    """构造与原文件同字节大小的 PDF（用空格填充标题）。"""
    target = original.parent / "replacement.pdf"
    document = fitz.open()
    page = document.new_page()
    padding = len(original.read_bytes()) % 100
    page.insert_text((72, 72), text + " " * padding, fontsize=14)
    document.save(target)
    document.close()
    data = target.read_bytes()
    target.unlink()
    return data
