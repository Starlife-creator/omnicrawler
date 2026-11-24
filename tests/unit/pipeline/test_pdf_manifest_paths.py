"""write_pdf_source_manifest 的交付路径解析（PDF 来源清单）。

为什么要有这个文件：该函数此前只有 1 条测试覆盖**成功路径**，而它读的是
DB 里的 `local_path` —— 那是**外部可写**的字段。

实测（2026-11-24）：它原本自己写了一遍「`Path(str(...))` + resolve + parents」判定，
与统一的 `require_workspace_path` 有两处口径差异：

* 相对 `local_path` 按**进程 cwd** 解析 ⇒ cwd ≠ workspace 时合法记录被误判越界；
* 路径**等于** workspace 时 `workspace not in path.parents` 成立（parents 不含自身）。

已改为复用统一工具。本文件钉住「相对路径按 workspace 解析」与「越界拒绝」。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from omnicrawler.pipeline_ops.provenance import write_pdf_source_manifest
from omnicrawler.state import StateStore


def _seed(state: StateStore, local_path: str, payload: bytes, *, run_id: str = "") -> None:
    """先建 run（artifacts 有外键），再插一条 PDF 记录。"""
    if not run_id:
        run_id = state.start_run("pdf-manifest", "configs/x.yaml")
    state.conn.execute(
        "INSERT INTO artifacts(run_id, request_fingerprint, source_url, local_path, "
        "content_type, size_bytes, sha256, created_at) VALUES(?,?,?,?,?,?,?,?)",
        (run_id, "fp-" + local_path, "https://example.test/a.pdf", local_path,
         "application/pdf", len(payload), hashlib.sha256(payload).hexdigest(), "2026-01-01T00:00:00Z"),
    )
    state.conn.commit()


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "artifacts" / "pdf").mkdir(parents=True)
    return ws


def test_relative_local_path_resolves_against_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DB 里存相对路径时，必须按 **workspace** 解析，与 cwd 无关。"""
    ws = _workspace(tmp_path)
    payload = b"%PDF-1.4 relative"
    (ws / "artifacts" / "pdf" / "a.pdf").write_bytes(payload)

    with StateStore(ws / "state.sqlite3") as state:
        _seed(state, "artifacts/pdf/a.pdf", payload)
        for cwd in (tmp_path, ws):
            monkeypatch.chdir(cwd)
            manifest = write_pdf_source_manifest(ws, state)
            assert manifest["documents"] == 1, manifest
            assert manifest["missing_files"] == 0


def test_absolute_local_path_inside_the_workspace_is_accepted(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    payload = b"%PDF-1.4 absolute"
    target = ws / "artifacts" / "pdf" / "b.pdf"
    target.write_bytes(payload)
    with StateStore(ws / "state.sqlite3") as state:
        _seed(state, str(target), payload)
        manifest = write_pdf_source_manifest(ws, state)
        assert manifest["documents"] == 1


@pytest.mark.parametrize("value", ["../outside.pdf", "artifacts/../../outside.pdf"])
def test_local_path_escaping_the_workspace_is_rejected(
    tmp_path: Path, value: str
) -> None:
    """越界必须拒绝 —— 修复「相对路径解析」不能顺手放宽边界。"""
    ws = _workspace(tmp_path)
    (tmp_path / "outside.pdf").write_bytes(b"%PDF-1.4 x")
    with StateStore(ws / "state.sqlite3") as state:
        _seed(state, value, b"%PDF-1.4 x")
        with pytest.raises(ValueError, match="越出工作区"):
            write_pdf_source_manifest(ws, state)


def test_missing_file_is_counted_not_raised(tmp_path: Path) -> None:
    """记录还在、文件被删 ⇒ 计入 missing，不抛错（清单要如实报告缺口）。"""
    ws = _workspace(tmp_path)
    with StateStore(ws / "state.sqlite3") as state:
        _seed(state, "artifacts/pdf/gone.pdf", b"%PDF-1.4 gone")
        manifest = write_pdf_source_manifest(ws, state)
        assert manifest["missing_files"] == 1
        assert manifest["documents"] == 0


def test_size_mismatch_is_rejected(tmp_path: Path) -> None:
    """文件大小与完成记录不一致 ⇒ 拒绝（数据被换过）。"""
    ws = _workspace(tmp_path)
    payload = b"%PDF-1.4 original"
    target = ws / "artifacts" / "pdf" / "c.pdf"
    target.write_bytes(payload)
    with StateStore(ws / "state.sqlite3") as state:
        _seed(state, str(target), payload + b"extra")
        with pytest.raises(ValueError, match="大小与完成记录不一致"):
            write_pdf_source_manifest(ws, state)


def test_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    """哈希不一致 ⇒ 拒绝（尺寸相同但内容被换，只有哈希能发现）。"""
    ws = _workspace(tmp_path)
    target = ws / "artifacts" / "pdf" / "d.pdf"
    target.write_bytes(b"%PDF-1.4 aaaa")
    with StateStore(ws / "state.sqlite3") as state:
        _seed(state, str(target), b"%PDF-1.4 bbbb")  # 同样长度，不同内容
        with pytest.raises(ValueError, match="哈希与完成记录不一致"):
            write_pdf_source_manifest(ws, state)
