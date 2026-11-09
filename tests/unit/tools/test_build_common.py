#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""`tools/build_common.py` 的判据测试。

覆盖三件事：
1. 清单形状与字段口径（schema / chrome / files 计数 / bytes 总计 / 逐文件 path·bytes·sha256）；
2. 两个**真缺口**的反向断言——清单不把自己算进去、哈希按分块流式读（不整文件入内存）；
3. 三平台制备脚本必须调用这份公共件，而不是各自再写一遍生成逻辑（防回退门禁）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools import build_common

BUNDLE_MANIFEST_NAME = "runtime-manifest.json"

_REPO_ROOT = Path(__file__).resolve().parents[3]
_WINDOWS_SCRIPT = _REPO_ROOT / "tools" / "prepare_windows_runtime.ps1"
_LINUX_SCRIPT = _REPO_ROOT / "packaging" / "prepare_linux_runtime.sh"
_MACOS_SCRIPT = _REPO_ROOT / "packaging" / "prepare_macos_runtime.sh"


def _make_runtime(root: Path) -> None:
    (root / "tesseract" / "tessdata").mkdir(parents=True)
    (root / "tesseract" / "tessdata" / "eng.traineddata").write_bytes(b"eng-blob")
    (root / "selenium").mkdir()
    (root / "selenium" / "chromedriver").write_bytes(b"driver")
    (root / "models" / "paddlex").mkdir(parents=True)
    (root / "models" / "paddlex" / "inference.json").write_text("{}", encoding="utf-8")


def test_manifest_shape_is_the_documented_one(tmp_path: Path) -> None:
    _make_runtime(tmp_path)
    manifest = build_common.build_bundle_manifest(tmp_path, chrome="/browsers/chrome.exe")
    assert manifest["schema"] == build_common.SCHEMA
    assert manifest["chrome"] == "/browsers/chrome.exe"
    assert manifest["files"] == 3
    assert manifest["bytes"] == len(b"eng-blob") + len(b"driver") + 2
    paths = [entry["path"] for entry in manifest["sha256"]]
    assert paths == [
        "models/paddlex/inference.json",
        "selenium/chromedriver",
        "tesseract/tessdata/eng.traineddata",
    ]


def test_manifest_records_streaming_sha256_of_every_file(tmp_path: Path) -> None:
    _make_runtime(tmp_path)
    manifest = build_common.build_bundle_manifest(tmp_path)
    by_path = {entry["path"]: entry for entry in manifest["sha256"]}
    expected = hashlib.sha256(b"eng-blob").hexdigest()
    assert by_path["tesseract/tessdata/eng.traineddata"]["sha256"] == expected
    assert by_path["tesseract/tessdata/eng.traineddata"]["bytes"] == len(b"eng-blob")


def test_manifest_skips_itself_so_reruns_are_reproducible(tmp_path: Path) -> None:
    """缺口①的反向断言：清单不得把 `runtime-manifest.json` 自己算进去。"""
    _make_runtime(tmp_path)
    first = build_common.write_bundle_manifest(tmp_path, chrome="chrome")
    written = json.loads((tmp_path / build_common.BUNDLE_MANIFEST).read_text(encoding="utf-8"))
    assert [entry["path"] for entry in written["sha256"]] == [
        entry["path"] for entry in first["sha256"]
    ]
    assert build_common.BUNDLE_MANIFEST not in {entry["path"] for entry in written["sha256"]}

    second = build_common.write_bundle_manifest(tmp_path, chrome="chrome")
    assert second["files"] == first["files"]
    assert second["bytes"] == first["bytes"]
    assert second["sha256"] == first["sha256"]


def test_manifest_does_not_read_whole_files_into_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """缺口②的反向断言：哈希必须分块读，不能 `read_bytes()` 一次性读入。"""
    _make_runtime(tmp_path)
    reads: list[int] = []
    original_open = Path.open

    class _Tracked:
        def __init__(self, handle):  # type: ignore[no-untyped-def]
            self._handle = handle

        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *exc):  # type: ignore[no-untyped-def]
            self._handle.close()
            return False

        def read(self, size: int = -1) -> bytes:
            reads.append(size)
            return self._handle.read(size)

    def _fake_open(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return _Tracked(original_open(self, *args, **kwargs))

    monkeypatch.setattr(Path, "open", _fake_open)
    build_common.build_bundle_manifest(tmp_path)
    assert reads, "哈希必须经过 Path.open 分块读取"
    assert all(size == build_common._HASH_CHUNK for size in reads), (
        f"出现非分块读取：{sorted(set(reads))}"
    )


def test_cli_writes_the_manifest(tmp_path: Path) -> None:
    _make_runtime(tmp_path)
    assert build_common.main(["manifest", "--runtime-root", str(tmp_path), "--chrome", "c"]) == 0
    written = json.loads((tmp_path / build_common.BUNDLE_MANIFEST).read_text(encoding="utf-8"))
    assert written["schema"] == build_common.SCHEMA
    assert written["chrome"] == "c"


def test_missing_runtime_root_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="运行时目录不存在"):
        build_common.build_bundle_manifest(tmp_path / "nope")


def test_no_script_spells_out_the_manifest_generation_again() -> None:
    """防回退门禁：三份脚本都必须调公共件，且不得再内嵌生成逻辑。

    判据只钉「生成逻辑」的特征，不钉各平台自己的合法哈希校验：
    `tools/prepare_windows_runtime.ps1` 里的 `$KNOWN_SHA256` / `Get-FileHash` 是**下载资产的
    哈希钉校验**（与清单生成无关），必须放行；真正禁止的是清单自己的字段与整文件读取哈希。
    """
    scripts = (_WINDOWS_SCRIPT, _LINUX_SCRIPT, _MACOS_SCRIPT)
    for script in scripts:
        assert script.is_file(), f"缺少制备脚本: {script}"
        text = script.read_text(encoding="utf-8")
        assert "build_common.py" in text, f"{script.name} 未调用 tools/build_common.py"
        assert " manifest " in text, f"{script.name} 未调用 manifest 子命令"
        # 清单字段字面量：PowerShell 是 `schema = 1`，bash 内嵌 Python 是 `"schema": 1`。
        assert "schema = 1" not in text, f"{script.name} 又自己拼了清单字段"
        assert '"schema": 1' not in text, f"{script.name} 又自己拼了清单字段"
        assert '"generated_at"' not in text, f"{script.name} 又自己拼了清单字段"
        # 整文件读入算哈希：运行时目录可达 GB 级，必须流式。
        assert "hashlib.sha256" not in text, f"{script.name} 又自己算文件哈希"
        assert "p.read_bytes()" not in text, f"{script.name} 又整文件读入内存"
        # 脚本只许在注释里提到清单文件名；任何可执行行出现它，就是把生成逻辑写回来了。
        for number, line in enumerate(text.splitlines(), start=1):
            if BUNDLE_MANIFEST_NAME in line:
                assert line.lstrip().startswith("#"), (
                    f"{script.name}:{number} 在可执行语句里提到了清单文件: {line.strip()}"
                )
