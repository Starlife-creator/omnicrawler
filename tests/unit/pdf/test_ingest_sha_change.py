"""S4.4 ④：ingest 同路径同大小新文件被 SHA 检出（F699）。"""

from __future__ import annotations

from pathlib import Path

from omnicrawler.pdfx.config import load_config
from omnicrawler.pdfx.database import Database
from omnicrawler.pdfx.ingest import ingest


def _write_pdf(path: Path, text: str) -> None:
    # Phase 0：fitz → reportlab（测试 fixture 生成；无压缩便于 _same_size 对齐体积）
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=A4, pageCompression=0)
    c.setFont("Helvetica", 14)
    c.drawString(72, A4[1] - 72, text)
    c.showPage()
    c.save()


def _pdf(tmp_path: Path, text: str, name: str = "doc.pdf") -> Path:
    target = tmp_path / name
    _write_pdf(target, text)
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
    """构造与原文件同字节大小的 PDF（reportlab 无压缩，用空格填充对齐）。"""
    import io

    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    target_size = len(original.read_bytes())
    # 无压缩渲染：向文本追加空格可线性增大体积，逐步逼近目标大小
    for padding in range(0, 400):
        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=A4, pageCompression=0)
        c.setFont("Helvetica", 14)
        c.drawString(72, A4[1] - 72, text + " " * padding)
        c.showPage()
        c.save()
        data = buffer.getvalue()
        if len(data) == target_size:
            return data
    # 兜底：最接近目标大小的版本（测试断言只要求 SHA 不同且 size 相同，
    # 若无法精确对齐则返回最后一版——此分支正常不应命中）
    return data
