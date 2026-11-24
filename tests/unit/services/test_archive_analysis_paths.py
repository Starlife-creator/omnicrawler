"""archive_analysis 的交付清单路径解析（JSONL 与格式 1 两种输入）。

为什么要有这个文件：这个模块覆盖率只有 9%，而它负责「按清单去读用户交付的文件」。
实测（2026-11-24）发现 `read_sources` 的 **JSONL 分支从来不能用**：

```python
path = Path(str(item.get("file_path", "")))      # 保留相对形态
if root not in path.resolve().parents:             # 解析到了 cwd，不是清单目录
    raise ValueError("交付文件越出清单目录")
relative = path.relative_to(root)                  # 相对路径必然抛错
```

两条失败叠在一起：

* cwd ≠ 清单目录 ⇒ 合法条目被误判「越出清单目录」；
* cwd == 清单目录 ⇒ 越过第一关，却在 `relative_to` 抛「not in the subpath」。

本文件钉住「相对路径相对**清单目录**解析」这条契约，并保证越界仍被拒绝。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from omnicrawler.services.archive_analysis import _verified_sources, read_sources


@pytest.fixture
def delivery(tmp_path: Path) -> Path:
    """一个带一份 PDF 的交付目录。"""
    root = tmp_path / "delivery"
    (root / "sub").mkdir(parents=True)
    (root / "doc1.pdf").write_bytes(b"%PDF-1.4 x")
    (root / "sub" / "doc2.pdf").write_bytes(b"%PDF-1.4 y")
    return root


def _manifest(root: Path, name: str, items: list[dict]) -> Path:
    path = root / name
    path.write_text("".join(json.dumps(item) + "\n" for item in items), encoding="utf-8")
    return path


def _verified_entry(relative: str) -> dict:
    return {"verification": "verified_file", "file_path": relative, "sha256": "x"}


def test_jsonl_relative_path_resolves_against_the_manifest_directory(
    delivery: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """合法相对路径必须成功 —— 与 cwd 无关。"""
    manifest = _manifest(delivery, "m.jsonl", [_verified_entry("doc1.pdf")])

    monkeypatch.chdir(tmp_path)  # cwd 在清单目录之外
    entries = read_sources(manifest)
    assert [entry["path"] for entry in entries] == ["doc1.pdf"]
    assert entries[0]["id"] == hashlib.sha256(b"doc1.pdf").hexdigest()

    monkeypatch.chdir(delivery)  # cwd 恰好在清单目录里
    assert [entry["path"] for entry in read_sources(manifest)] == ["doc1.pdf"]


def test_jsonl_nested_relative_path_keeps_posix_form(
    delivery: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """子目录条目用 posix 分隔符回报（跨平台一致）。"""
    manifest = _manifest(delivery, "m.jsonl", [_verified_entry("sub/doc2.pdf")])
    monkeypatch.chdir(delivery)
    entries = read_sources(manifest)
    assert entries[0]["path"] == "sub/doc2.pdf"


@pytest.mark.parametrize(
    "relative",
    ["../outside.pdf", "..", "../delivery2/x.pdf"],
)
def test_jsonl_entry_outside_the_manifest_directory_is_rejected(
    delivery: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str
) -> None:
    """越界必须拒绝 —— 修好相对路径**不能**顺手放宽边界。"""
    (tmp_path / "outside.pdf").write_bytes(b"x")
    manifest = _manifest(delivery, "m.jsonl", [_verified_entry(relative)])
    monkeypatch.chdir(delivery)
    with pytest.raises(ValueError, match="越出清单目录"):
        read_sources(manifest)


def test_jsonl_absolute_path_outside_is_rejected(
    delivery: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """绝对路径指向外部同样拒绝（不能靠「写成绝对路径」绕过）。"""
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"x")
    manifest = _manifest(delivery, "m.jsonl", [_verified_entry(str(outside))])
    monkeypatch.chdir(delivery)
    with pytest.raises(ValueError, match="越出清单目录"):
        read_sources(manifest)


def test_jsonl_empty_file_path_is_rejected(delivery: Path) -> None:
    manifest = _manifest(delivery, "m.jsonl", [_verified_entry("")])
    with pytest.raises(ValueError, match="file_path"):
        read_sources(manifest)


def test_jsonl_requires_the_verification_marker(delivery: Path) -> None:
    """没有 "verified_file" 标记的条目不得当成已核验交付。"""
    manifest = _manifest(delivery, "m.jsonl", [{"file_path": "doc1.pdf", "sha256": "x"}])
    with pytest.raises(ValueError, match="文件核验标记"):
        read_sources(manifest)


def test_verified_sources_accepts_a_relative_pdf_and_checks_the_hash(
    delivery: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """格式 1 清单：相对路径 + 哈希必须一致。"""
    payload = (delivery / "doc1.pdf").read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    manifest = delivery / "analysis.json"
    manifest.write_text(
        json.dumps({"format": 1, "sources": [{"id": "a", "path": "doc1.pdf", "sha256": digest}]}),
        encoding="utf-8",
    )
    monkeypatch.chdir(delivery)
    assert len(_verified_sources(manifest)) == 1


def test_verified_sources_rejects_a_hash_mismatch(
    delivery: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """哈希不一致必须拒绝：这是「文件没被改过」的唯一判据。"""
    manifest = delivery / "analysis.json"
    manifest.write_text(
        json.dumps({"format": 1, "sources": [{"id": "a", "path": "doc1.pdf", "sha256": "wrong"}]}),
        encoding="utf-8",
    )
    monkeypatch.chdir(delivery)
    with pytest.raises(ValueError, match="哈希不一致"):
        _verified_sources(manifest)
