import hashlib
import shutil

from omnicrawler.document_ir import parse_document
from omnicrawler.document_ir.base import DocumentIR


def test_nodes_remain_stable_after_workspace_move_and_retain_section_parents(tmp_path):
    path = tmp_path / "source.html"
    path.write_text("<main><h1>Root</h1><p>Repeated</p><h2>Child</h2><p>Repeated</p>"
                    "<table><tr><td>value</td></tr></table><h1>Next</h1><p>End</p></main>")
    first = parse_document(path)
    moved = tmp_path / "中文 目录" / "moved.html"
    moved.parent.mkdir()
    shutil.copyfile(path, moved)
    second = parse_document(moved)
    assert [node.node_id for node in first.blocks] == [node.node_id for node in second.blocks]
    assert first.metadata["source_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert len({node.node_id for node in first.blocks}) == len(first.blocks)
    root, paragraph, child, nested, table, next_section, end = first.blocks
    assert paragraph.parent_id == child.parent_id == root.node_id
    assert nested.parent_id == table.parent_id == child.node_id
    assert not next_section.parent_id
    assert end.parent_id == next_section.node_id


def test_promoting_title_retains_node_identity(tmp_path):
    path = tmp_path / "source.txt"
    path.write_text("Title\nBody")
    document = DocumentIR(path, ".txt")
    document.add_paragraph("Title", heading_level=1)
    document.add_paragraph("Body")
    title, body = document.blocks
    document.promote_first_paragraph_to_title()
    assert document.metadata["title_node_id"] == title.node_id
    assert document.blocks[0].node_id == body.node_id
    assert document.blocks[0].parent_id == title.node_id
    assert document.blocks[0].index == 0
