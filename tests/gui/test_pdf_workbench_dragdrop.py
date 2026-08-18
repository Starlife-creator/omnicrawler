"""P1-4：PDF 工作台拖放与"添加文件"入口的单元测试。

覆盖：
- `_stage_dropped_files`：硬链接/复制归集、旧内容清理、同名冲突加序号
- `dragEnterEvent`：接受 PDF 文件/目录，拒绝非 PDF 与非本地 URL
- `dropEvent`：拖入目录触发扫描；拖入 PDF 文件暂存后扫描；空拖放忽略
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QMimeData, QUrl
from PyQt6.QtWidgets import QApplication

from omnicrawler.gui.views.pdf_workbench import PdfWorkbenchView

_app = QApplication.instance() or QApplication([])


def _make_view() -> PdfWorkbenchView:
    """构造工作台实例（不进入主窗口）。"""
    return PdfWorkbenchView()


def _url_mime(paths: list[Path]) -> QMimeData:
    """构造含本地文件 URL 的 QMimeData。"""
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    return mime


class _FakeDragEnterEvent:
    """模拟 QDragEnterEvent，仅暴露 dragEnterEvent/dragMoveEvent 需要的接口。"""

    def __init__(self, mime: QMimeData) -> None:
        self._mime = mime
        self._accepted = False
        self._ignored = False

    def mimeData(self) -> QMimeData:
        return self._mime

    def acceptProposedAction(self) -> None:
        self._accepted = True

    def ignore(self) -> None:
        self._ignored = True

    @property
    def accepted(self) -> bool:
        return self._accepted

    @property
    def ignored(self) -> bool:
        return self._ignored


class _FakeDropEvent(_FakeDragEnterEvent):
    """模拟 QDropEvent，复用同一接口。"""


# ── _stage_dropped_files ────────────────────────────────────────────


def test_stage_files_creates_hardlinks_or_copies(tmp_path: Path) -> None:
    """拖入的 PDF 应以硬链接或复制形式出现在暂存目录。"""
    src1 = tmp_path / "a.pdf"
    src1.write_bytes(b"%PDF-1.4 a")
    src2 = tmp_path / "b.pdf"
    src2.write_bytes(b"%PDF-1.4 b")

    staging_root = tmp_path / "staging_root"
    view = _make_view()
    with patch(
        "omnicrawler.core.runtime_paths.portable_data_root",
        return_value=staging_root,
    ):
        staging = view._stage_dropped_files([src1, src2])

    assert staging is not None
    files = sorted(p.name for p in staging.iterdir())
    assert files == ["a.pdf", "b.pdf"]
    # 内容一致（硬链接或复制都应可读）
    assert (staging / "a.pdf").read_bytes() == b"%PDF-1.4 a"
    assert (staging / "b.pdf").read_bytes() == b"%PDF-1.4 b"


def test_stage_files_clears_previous_content(tmp_path: Path) -> None:
    """第二次调用应清空暂存目录，避免上次残留干扰本次扫描。"""
    src = tmp_path / "new.pdf"
    src.write_bytes(b"%PDF-1.4 new")

    staging_root = tmp_path / "staging_root"
    staging_dir = staging_root / ".omnicrawler" / "pdf-workbench" / "dropped"
    staging_dir.mkdir(parents=True)
    (staging_dir / "old.pdf").write_bytes(b"old residue")

    view = _make_view()
    with patch(
        "omnicrawler.core.runtime_paths.portable_data_root",
        return_value=staging_root,
    ):
        staging = view._stage_dropped_files([src])

    assert staging is not None
    names = [p.name for p in staging.iterdir()]
    assert "old.pdf" not in names
    assert "new.pdf" in names


def test_stage_files_resolves_name_conflict(tmp_path: Path) -> None:
    """同名文件冲突时自动加序号后缀，两个文件都保留。"""
    src1 = tmp_path / "report.pdf"
    src1.write_bytes(b"v1")
    # 不同目录下的同名文件
    subdir = tmp_path / "sub"
    subdir.mkdir()
    src2 = subdir / "report.pdf"
    src2.write_bytes(b"v2")

    staging_root = tmp_path / "staging_root"
    view = _make_view()
    with patch(
        "omnicrawler.core.runtime_paths.portable_data_root",
        return_value=staging_root,
    ):
        staging = view._stage_dropped_files([src1, src2])

    assert staging is not None
    names = sorted(p.name for p in staging.iterdir())
    assert "report.pdf" in names
    assert "report (1).pdf" in names
    # 两个内容都保留
    contents = {(staging / n).read_bytes() for n in names}
    assert contents == {b"v1", b"v2"}


# ── dragEnterEvent ──────────────────────────────────────────────────


def test_drag_enter_accepts_pdf_file(tmp_path: Path) -> None:
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    event = _FakeDragEnterEvent(_url_mime([pdf]))
    _make_view().dragEnterEvent(event)
    assert event.accepted
    assert not event.ignored


def test_drag_enter_accepts_directory(tmp_path: Path) -> None:
    event = _FakeDragEnterEvent(_url_mime([tmp_path]))
    _make_view().dragEnterEvent(event)
    assert event.accepted


def test_drag_enter_rejects_non_pdf_file(tmp_path: Path) -> None:
    txt = tmp_path / "notes.txt"
    txt.write_text("hello", encoding="utf-8")
    event = _FakeDragEnterEvent(_url_mime([txt]))
    _make_view().dragEnterEvent(event)
    assert event.ignored
    assert not event.accepted


def test_drag_enter_rejects_non_local_url() -> None:
    mime = QMimeData()
    mime.setUrls([QUrl("https://example.com/doc.pdf")])
    event = _FakeDragEnterEvent(mime)
    _make_view().dragEnterEvent(event)
    assert event.ignored


def test_drag_enter_accepts_when_mix_of_pdf_and_non_pdf(tmp_path: Path) -> None:
    """混合拖放中只要有一个 PDF 文件或目录就接受。"""
    pdf = tmp_path / "ok.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    txt = tmp_path / "skip.txt"
    txt.write_text("x", encoding="utf-8")
    event = _FakeDragEnterEvent(_url_mime([txt, pdf]))
    _make_view().dragEnterEvent(event)
    assert event.accepted


# ── dragMoveEvent ───────────────────────────────────────────────────


def test_drag_move_accepts_urls() -> None:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile("/tmp/x.pdf")])
    event = _FakeDragEnterEvent(mime)
    _make_view().dragMoveEvent(event)
    assert event.accepted


def test_drag_move_ignores_empty_mime() -> None:
    event = _FakeDragEnterEvent(QMimeData())
    _make_view().dragMoveEvent(event)
    assert event.ignored


# ── dropEvent ───────────────────────────────────────────────────────


def test_drop_directory_sets_input_and_triggers_scan(tmp_path: Path) -> None:
    """拖入目录应设置 dir_input 并触发扫描。"""
    target = tmp_path / "pdfs"
    target.mkdir()
    (target / "a.pdf").write_bytes(b"%PDF-1.4 a")

    view = _make_view()
    triggered: dict[str, object] = {}

    def fake_scan() -> None:
        triggered["called"] = True

    with patch.object(view, "_scan_directory", fake_scan):
        event = _FakeDropEvent(_url_mime([target]))
        view.dropEvent(event)

    assert event.accepted
    assert view._dir_input.text() == str(target)
    assert triggered.get("called") is True


def test_drop_pdf_files_stages_and_scans(tmp_path: Path) -> None:
    """拖入 PDF 文件应暂存到 dropped 目录、设置 dir_input、触发扫描。"""
    src = tmp_path / "dragged.pdf"
    src.write_bytes(b"%PDF-1.4 dragged")

    staging_root = tmp_path / "staging_root"
    view = _make_view()
    triggered: dict[str, object] = {}

    def fake_scan() -> None:
        triggered["called"] = True

    with (
        patch(
            "omnicrawler.core.runtime_paths.portable_data_root",
            return_value=staging_root,
        ),
        patch.object(view, "_scan_directory", fake_scan),
    ):
        event = _FakeDropEvent(_url_mime([src]))
        view.dropEvent(event)

    assert event.accepted
    assert triggered.get("called") is True
    staging_dir = Path(view._dir_input.text())
    assert staging_dir.exists()
    assert (staging_dir / "dragged.pdf").exists()


def test_drop_ignores_non_pdf_non_dir(tmp_path: Path) -> None:
    """拖入纯非 PDF 文件应被忽略，不触发扫描。"""
    txt = tmp_path / "readme.txt"
    txt.write_text("hi", encoding="utf-8")

    view = _make_view()
    triggered: dict[str, object] = {}

    def fake_scan() -> None:
        triggered["called"] = True

    with patch.object(view, "_scan_directory", fake_scan):
        event = _FakeDropEvent(_url_mime([txt]))
        view.dropEvent(event)

    assert event.ignored
    assert triggered.get("called") is None


def test_drop_directory_takes_precedence_over_files(tmp_path: Path) -> None:
    """同时拖入目录和文件时，目录优先（首个目录胜出）。"""
    target = tmp_path / "dir"
    target.mkdir()
    pdf = tmp_path / "loose.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    view = _make_view()
    triggered: dict[str, object] = {}

    def fake_scan() -> None:
        triggered["called"] = True

    with patch.object(view, "_scan_directory", fake_scan):
        event = _FakeDropEvent(_url_mime([pdf, target]))
        view.dropEvent(event)

    assert event.accepted
    assert view._dir_input.text() == str(target)
    assert triggered.get("called") is True


# ── 视图初始化 ──────────────────────────────────────────────────────


def test_view_accepts_drops_by_default() -> None:
    """P1-4：工作台应默认启用拖放。"""
    view = _make_view()
    assert view.acceptDrops() is True


def test_view_has_add_files_button() -> None:
    """P1-4：目录选择行应包含"添加文件..."按钮。"""
    from PyQt6.QtWidgets import QPushButton

    view = _make_view()
    buttons = view.findChildren(QPushButton)
    texts = [b.text() for b in buttons]
    assert any("添加文件" in t for t in texts), f"未找到添加文件按钮，现有按钮: {texts}"
