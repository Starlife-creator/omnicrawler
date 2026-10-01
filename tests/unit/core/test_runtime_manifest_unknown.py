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


def test_runtime_state_dir_excluded_from_manifest_and_unknown_scan(tmp_path: Path) -> None:
    """★ 运行期状态目录 `.omnicrawler/` 与 `logs/` 同理：**创建与校验两侧都排除**。

    由来（2026-10-01 release 预检实测）：构建脚本在载荷根跑冒烟，冻结应用把状态写到
    exe 同级的 `.omnicrawler/` ⇒ 它被打进包、还会被清单声明；而打包侧一旦清掉该残留，
    若清单仍声明它，`runtime-verify` 就会判 missing/unknown。两侧一致才不会两头红。
    """
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "app.exe").write_bytes(b"trusted")
    state = runtime / ".omnicrawler"
    state.mkdir()
    (state / "runtime-status.json").write_text('{"tesseract": "ok"}', encoding="utf-8")

    manifest = create_runtime_manifest(runtime)
    assert not [name for name in manifest["files"] if ".omnicrawler" in name], manifest["files"]
    report = verify_runtime_manifest(runtime)
    assert report["ok"] is True, report
    assert report["unknown"] == []

    # macOS 形态：运行期状态落在 `OmniCrawler.app/Contents/MacOS/.omnicrawler/`（任意层级都排除）
    nested = runtime / "OmniCrawler.app" / "Contents" / "MacOS" / ".omnicrawler"
    nested.mkdir(parents=True)
    (nested / "runtime-status.json").write_text("{}", encoding="utf-8")
    report = verify_runtime_manifest(runtime)
    assert report["ok"] is True, report
    assert report["unknown"] == []
