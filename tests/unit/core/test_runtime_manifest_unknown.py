"""S3.2.3：完整性校验检出"新增未知文件"。"""

from __future__ import annotations

from pathlib import Path

from omnicrawler.core.runtime_manifest import create_runtime_manifest, verify_runtime_manifest


def test_unknown_file_detected(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "app.exe").write_bytes(b"trusted")
    create_runtime_manifest(runtime)
    report = verify_runtime_manifest(runtime)
    assert report["ok"] is True
    assert report["unknown"] == []

    # 注入清单外新增文件（如 DLL 侧加载的旁路物）
    (runtime / "unexpected.dll").write_bytes(b"evil")
    report = verify_runtime_manifest(runtime)
    assert report["ok"] is False
    assert "unexpected.dll" in report["unknown"]

    # 清理后恢复
    (runtime / "unexpected.dll").unlink()
    assert verify_runtime_manifest(runtime)["ok"] is True


def test_missing_and_corrupt_still_detected(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime2"
    runtime.mkdir()
    (runtime / "a.bin").write_bytes(b"data")
    create_runtime_manifest(runtime)
    (runtime / "a.bin").write_bytes(b"tampered")
    (runtime / "b.bin").write_bytes(b"extra")
    report = verify_runtime_manifest(runtime)
    assert report["corrupt"] == ["a.bin"]
    assert "b.bin" in report["unknown"]
