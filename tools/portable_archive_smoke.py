"""W4.2: extract an *uploaded* portable archive and smoke-test it with its own entry point.

Why this exists (what it adds on top of the build-time checks)
--------------------------------------------------------------

All three ``build_*.{sh,ps1}`` scripts already run, **inside the build job** and **against the
build tree**:

1. ``check_release_integrity --portable-* --portable-deep`` -- container-level validation;
2. ``portable_smoke_test.py <releaseRoot>`` -- local fixture site + packaged entry point.

That leaves two blind spots, and closing them is the whole point of this tool:

* **the archive round trip is unverified** -- users download the *archive*, not the build tree.
  Whether ``tar.xz`` / ``tar.gz`` / ``zip`` / ``dmg`` survives pack -> upload -> download ->
  unpack is never exercised by the build job, yet that is exactly the artifact that ships.
* **the build environment is not isolated** -- the build job carries its own PATH, env vars and
  caches, which can mask "only reproducible on a clean machine" portability defects.  (The macOS
  ``@rpath`` lookup that resolved to cv2's vendored ``libcrypto`` was precisely of that nature.)

So this runs on a **fresh runner**: locate archive -> extract -> locate release root -> reuse
``portable_smoke_test.run_smoke_test()`` (nothing reimplemented) -> write a manifest
(name / size / sha256 / release root) so the evidence is retained as an artifact.

Notes
-----

* This tool **never downloads** anything -- downloading is ``actions/download-artifact``'s job,
  which keeps the "no portable downloads on the local machine" decision intact.
* **All output is ASCII only.**  The job also runs on ``windows-latest``, where a cp1252 console
  turns any non-ASCII character into ``UnicodeEncodeError`` -- the exact failure mode that once
  broke the ``quality`` Windows job.  Guarded by
  ``tests/unit/tools/test_portable_archive_smoke.py`` (cp1252 console + ASCII-only output).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any

_TOOLS_DIR = Path(__file__).resolve().parent

# 平台标记（归档名里必须出现）与容器优先级。
#
# ★ 为什么必须带平台标记：最初用的是 `*Portable*.tar.gz` 这类宽松 glob，结果
# **macOS 会把 Linux 的 `OmniCrawler-<ver>-Linux-Portable-*.tar.gz` 挑走**
# （本地守卫实测抓到）—— CI 上就是"macOS runner 去冒烟 Linux 归档"，而且因为两者都能解压，
# 失败信息不会指向"挑错了包"这个真正的原因。
#
# macOS 优先 `.tar.gz` 而非 `.dmg`：两者装同一份发行树，tar 无需挂载步骤；
# `.dmg` 容器本身的校验由构建期 `check_release_integrity` 负责。
_PLATFORM_TOKENS: dict[str, str] = {"linux": "Linux", "windows": "Windows", "macos": "macOS"}
_CONTAINER_ORDER: dict[str, tuple[str, ...]] = {
    "linux": (".tar.xz", ".tar.gz", ".zip"),
    "windows": (".zip",),
    "macos": (".tar.gz", ".dmg", ".zip"),
}
_TAR_SUFFIXES = (".tar.xz", ".tar.gz", ".tgz", ".tar.bz2")


def _patterns_for(platform: str) -> tuple[str, ...]:
    """该平台的归档查找顺序（单一来源：平台标记 × 容器优先级）。"""
    token = _PLATFORM_TOKENS.get(platform)
    if token is None:
        raise ValueError(f"unknown platform: {platform!r}")
    return tuple(f"*{token}-Portable*{suffix}" for suffix in _CONTAINER_ORDER[platform])



def _load_smoke_module() -> Any:
    """Import the sibling ``portable_smoke_test`` by path (``tools/`` is not a package)."""
    path = _TOOLS_DIR / "portable_smoke_test.py"
    spec = importlib.util.spec_from_file_location("_portable_smoke_test", path)
    if spec is None or spec.loader is None:  # pragma: no cover - 只在文件缺失时发生
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def find_archive(directory: Path, platform: str) -> Path:
    """返回该平台要验证的便携归档（按 `_patterns_for()` 的顺序取第一个命中）。"""
    listing = sorted(p.name for p in directory.iterdir()) if directory.is_dir() else []
    for pattern in _patterns_for(platform):
        matches = sorted(p for p in directory.glob(pattern) if p.is_file())
        if matches:
            return matches[0]
    raise FileNotFoundError(
        f"no portable archive for platform={platform} in {directory}; found: {listing}"
    )


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - argv is built from literals plus paths we own
        command,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _extract_dmg(archive: Path, into: Path) -> None:
    """Unpack a ``.dmg`` (macOS only) by mounting it read-only and copying the payload out."""
    if sys.platform != "darwin":
        raise RuntimeError(".dmg can only be unpacked on macOS")
    mount = into / "_dmg_mount"
    mount.mkdir(parents=True, exist_ok=True)
    _run(["hdiutil", "attach", str(archive), "-nobrowse", "-readonly", "-mountpoint", str(mount)])
    try:
        for item in sorted(mount.iterdir()):
            destination = into / item.name
            if destination.exists():
                continue
            if item.is_dir():
                shutil.copytree(item, destination, symlinks=True)
            else:
                shutil.copy2(item, destination)
    finally:
        _run(["hdiutil", "detach", str(mount), "-force"], check=False)


def extract(archive: Path, into: Path) -> None:
    """Extract *archive* into *into* (``.zip`` / ``.tar.*`` / ``.dmg``)."""
    into.mkdir(parents=True, exist_ok=True)
    name = archive.name.lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(into)
        return
    if name.endswith(_TAR_SUFFIXES):
        with tarfile.open(archive) as bundle:
            bundle.extractall(into, filter="data")
        return
    if name.endswith(".dmg"):
        _extract_dmg(archive, into)
        return
    raise ValueError(f"unsupported archive format: {archive.name}")


def locate_release_root(extracted: Path) -> Path:
    """Find the directory holding the packaged entry point.

    Resolution is delegated to ``portable_smoke_test._resolve_executable`` on purpose: the smoke
    test and this locator must never disagree about which binary is "the" entry point.
    """
    smoke = _load_smoke_module()
    candidates = [extracted, *(p for p in sorted(extracted.rglob("*")) if p.is_dir())]
    for candidate in candidates:
        if smoke._resolve_executable(candidate) is not None:  # noqa: SLF001 - 同一套解析
            return candidate
    raise FileNotFoundError(f"no packaged entry point found below {extracted}")


def run(
    downloaded: Path,
    platform: str,
    edition: str,
    *,
    keep: Path | None = None,
) -> dict[str, Any]:
    """Full pipeline: find -> extract -> locate -> smoke.  Returns the manifest payload."""
    archive = find_archive(downloaded, platform)
    print(f"portable archive: {archive.name} ({archive.stat().st_size} bytes)")
    digest = sha256_of(archive)
    print(f"sha256: {digest}")

    temporary: tempfile.TemporaryDirectory[str] | None = None
    if keep is None:
        temporary = tempfile.TemporaryDirectory(prefix="omnicrawler-archive-smoke-")
        extract_dir = Path(temporary.name)
    else:
        extract_dir = keep
    try:
        extract(archive, extract_dir)
        release_root = locate_release_root(extract_dir)
        print(f"release root: {release_root}")
        _load_smoke_module().run_smoke_test(release_root, edition)
        relative_root = "." if release_root == extract_dir else str(release_root.relative_to(extract_dir))
        return {
            "platform": platform,
            "edition": edition,
            "archive": {
                "name": archive.name,
                "size_bytes": archive.stat().st_size,
                "sha256": digest,
            },
            "release_root": relative_root,
            "smoke": "ok",
        }
    finally:
        if temporary is not None:
            temporary.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("downloaded", type=Path, help="directory holding the downloaded artifacts")
    parser.add_argument("--platform", choices=sorted(_PLATFORM_TOKENS), required=True)
    parser.add_argument("--edition", choices=["Standard", "Full"], default="Full")
    parser.add_argument("--manifest", type=Path, help="also write the manifest JSON here")
    parser.add_argument(
        "--extract-dir", type=Path, help="extract here instead of a temp dir (debugging)"
    )
    args = parser.parse_args()

    manifest = run(args.downloaded, args.platform, args.edition, keep=args.extract_dir)
    # keep_ascii: the manifest must stay printable on a cp1252 console (Windows runner).
    payload = json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True)
    print(payload)
    if args.manifest is not None:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(payload + "\n", encoding="utf-8")
    print("archive-level portable smoke: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
