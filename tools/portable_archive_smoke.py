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
import os
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


def _run(
    command: list[str], *, check: bool = True, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - argv is built from literals plus paths we own
        command,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
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


def runtime_verify(release_root: Path) -> None:
    """产品自带的 `runtime-verify`（RUNTIME-MANIFEST **双向**核对）。

    三个构建脚本都在**构建树**上跑它（`build_{windows.ps1,linux.sh,macos.sh}`）。为什么这里还要
    在**解压后的归档**上再跑一次：macOS 的 dmg 是磁盘镜像，**纯 Python 读不了其内部**
    （`build_macos.sh` 注释原话），所以"归档往返是否丢了文件"在构建期根本无从回答 ——
    而这正是 W4.2 第一次派发就撞上的事：同一个包构建树通过、经 dmg 往返后 Full OCR self-test 失败。

    ★ 失败时**原样抛出产品给的清单**（它按文件列出缺失/被篡改项），而不是只说一句"冒烟失败"。
    """
    smoke = _load_smoke_module()
    executable = smoke._resolve_executable(release_root)
    if executable is None:
        raise FileNotFoundError(f"no packaged entry point in {release_root}")
    completed = _run([str(executable), "runtime-verify", "--root", str(release_root)], check=False)
    output = f"{completed.stdout}\n{completed.stderr}".strip()
    if completed.returncode != 0:
        raise RuntimeError(
            f"runtime-verify failed on the extracted archive (rc={completed.returncode}):\n"
            f"{output[-8000:]}"
        )
    print(f"runtime-verify: OK  {output.splitlines()[0][:110] if output else ''}".rstrip())


# I1：Linux 便携包内附带的用户级安装件。清单口径与 `tools/check_linux_delivery.py`、
# `check_release_integrity._PORTABLE_REQUIRED_EXTRA["linux"]` 保持一致。
_HICOLOR_ICON_SIZES: tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)


def linux_install_smoke(release_root: Path, workdir: Path) -> dict[str, Any]:
    """在**解压后的真产物**上跑一遍 `install-user.sh`，断言落位与 `.desktop` 契约。

    为什么必须在归档层做：脚本读的是**产物内**的 `installer/` 与 hicolor 图标，而
    "装配时到底复制进去没有"在构建树上看不出来 —— tar 往返丢文件正是 W4.2 第一次
    派发就撞上的事。这里用 `HOME=<tmp>` 做一次真安装，然后逐条断言：

    * `.desktop` 落到 `$HOME/.local/share/applications/`
    * `Exec=` 是**绝对路径**、目标是**产物内的 GUI 二进制**、且真的存在
      （就地注册会留下死链：用户清理下载目录后应用失效）
    * hicolor 的 7 个尺寸都在
    * `desktop-file-validate` 通过；**没装则可见跳过**（写进 manifest，不静默通过）
    """
    installer = release_root / "installer" / "install-user.sh"
    if not installer.is_file():
        raise FileNotFoundError(f"no installer in the extracted archive: {installer}")

    home = workdir / "home"
    prefix = workdir / "prefix"
    home.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "HOME": str(home)}
    env.pop("XDG_DATA_HOME", None)

    completed = _run(
        ["/bin/bash", str(installer), "--prefix", str(prefix), "--no-desktop-database"],
        check=False,
        env=env,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"install-user.sh failed on the extracted archive (rc={completed.returncode}):\n"
            f"{(completed.stdout + completed.stderr)[-4000:]}"
        )

    desktop = home / ".local" / "share" / "applications" / "omnicrawler.desktop"
    if not desktop.is_file():
        raise RuntimeError(f"install-user.sh did not write the desktop entry: {desktop}")
    text = desktop.read_text(encoding="utf-8")
    exec_lines = [line for line in text.splitlines() if line.startswith("Exec=")]
    if not exec_lines:
        raise RuntimeError("desktop entry has no Exec= line")
    exec_target = Path(exec_lines[0].removeprefix("Exec="))
    if not exec_target.is_absolute():
        raise RuntimeError(f"desktop Exec= must be absolute, got {exec_target}")
    if not exec_target.is_file():
        raise RuntimeError(f"desktop Exec= points at a missing entry point: {exec_target}")
    if exec_target != prefix / "OmniCrawler":
        raise RuntimeError(f"desktop Exec= must target the packaged GUI binary, got {exec_target}")
    if "Icon=omnicrawler" not in text:
        raise RuntimeError("desktop entry must declare Icon=omnicrawler")

    hicolor = home / ".local" / "share" / "icons" / "hicolor"
    missing = [
        f"{size}x{size}"
        for size in _HICOLOR_ICON_SIZES
        if not (hicolor / f"{size}x{size}" / "apps" / "omnicrawler.png").is_file()
    ]
    if missing:
        raise RuntimeError(f"hicolor icons missing after install: {', '.join(missing)}")

    validator = shutil.which("desktop-file-validate")
    if validator is None:
        validate = "skipped: desktop-file-utils not installed"
        print(f"linux install smoke: {validate}")
    else:
        validation = _run([validator, str(desktop)], check=False)
        if validation.returncode != 0:
            raise RuntimeError(
                "desktop-file-validate rejected the entry:\n"
                f"{(validation.stdout + validation.stderr)[-2000:]}"
            )
        validate = "ok"

    print(
        f"linux install smoke: OK (desktop entry + {len(_HICOLOR_ICON_SIZES)} hicolor icons, "
        f"desktop-file-validate={validate})"
    )
    return {
        "desktop_entry": "ok",
        "exec_absolute_and_present": True,
        "hicolor_icons": len(_HICOLOR_ICON_SIZES),
        "desktop_file_validate": validate,
    }


def run(
    downloaded: Path,
    platform: str,
    edition: str,
    *,
    keep: Path | None = None,
) -> dict[str, Any]:
    """Full pipeline: find -> extract -> locate -> runtime-verify -> smoke."""
    archive = find_archive(downloaded, platform)
    print(f"portable archive: {archive.name} ({archive.stat().st_size} bytes)")
    digest = sha256_of(archive)
    print(f"sha256: {digest}")

    temporary: tempfile.TemporaryDirectory[str] | None = None
    install_work: tempfile.TemporaryDirectory[str] | None = None
    if keep is None:
        temporary = tempfile.TemporaryDirectory(prefix="omnicrawler-archive-smoke-")
        extract_dir = Path(temporary.name)
    else:
        extract_dir = keep
    try:
        extract(archive, extract_dir)
        release_root = locate_release_root(extract_dir)
        print(f"release root: {release_root}")
        # 先做清单双向核对：若归档真的丢了文件，这条会**指名道姓**列出来
        # （比冒烟抛一个笼统的 self-test 失败信息有用得多）。
        runtime_verify(release_root)
        _load_smoke_module().run_smoke_test(release_root, edition)
        relative_root = "." if release_root == extract_dir else str(release_root.relative_to(extract_dir))
        manifest: dict[str, Any] = {
            "platform": platform,
            "edition": edition,
            "archive": {
                "name": archive.name,
                "size_bytes": archive.stat().st_size,
                "sha256": digest,
            },
            "release_root": relative_root,
            "runtime_verify": "ok",
            "smoke": "ok",
        }
        if platform == "linux":
            # I1：安装脚本只随 Linux 包分发，故该键只对 linux 出现 —— 不是静默跳过，
            # 而是"这个交付形态只在 Linux 上存在"。
            install_work = tempfile.TemporaryDirectory(prefix="omnicrawler-install-smoke-")
            manifest["install_smoke"] = linux_install_smoke(
                release_root, Path(install_work.name)
            )
        return manifest
    finally:
        if temporary is not None:
            temporary.cleanup()
        if install_work is not None:
            install_work.cleanup()


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
