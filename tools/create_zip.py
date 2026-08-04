"""Create reproducible, ZIP64-capable release archives with exclusions."""

from __future__ import annotations

import argparse
import datetime
import os
import zipfile
from pathlib import Path

DEFAULT_EXCLUDES = {
    ".git", ".venv", ".build-venv", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "__pycache__", "build", "build_cache", "build_dist", "release", "dist", "artifacts", "work", "e2e-artifacts", ".test-tmp", ".t", ".pip-cache", ".runtime",
    # A checkout can also host a retained portable Windows release at its root.
    # Those directories are runtime payloads, never source-distribution inputs.
    "_internal", "browsers", "runtime", "data",
}

PORTABLE_ROOT_FILES = {
    "OmniCrawler.exe",
    "omnicrawl.exe",
    "omnicrawl-worker.exe",
    "OmniCrawler-Launcher.bat",
    "PORTABLE.flag",
    "EDITION.txt",
    "PORTABLE_README.txt",
    "RUNTIME-MANIFEST.json",
    "RELEASE-INFO.json",
    "CAPABILITIES.json",
    "SBOM.json",
    "THIRD_PARTY_NOTICES.md",
}


def _archive_files(source: Path, *, clean_source: bool) -> list[Path]:
    """Return source files in reproducible order without descending into excluded trees."""
    files: list[Path] = []
    for directory, child_dirs, child_files in os.walk(source):
        directory_path = Path(directory)
        if clean_source:
            child_dirs[:] = [
                name
                for name in child_dirs
                if name not in DEFAULT_EXCLUDES and not name.endswith(".egg-info")
            ]
        for name in child_files:
            path = directory_path / name
            relative = path.relative_to(source)
            if clean_source and len(relative.parts) == 1 and relative.name in PORTABLE_ROOT_FILES:
                continue
            if clean_source and relative.name in {".coverage", "coverage.json", "coverage.xml"}:
                continue
            if clean_source and (
                (relative.name.startswith("coverage-") and relative.suffix == ".json")
                or relative.name.startswith("visual-qa-")
            ):
                continue
            if clean_source and path.suffix.lower() in {".pyc", ".pyo"}:
                continue
            files.append(path)
    return sorted(files, key=lambda item: item.as_posix().casefold())


def create_zip(source: Path, output: Path, root_name: str, clean_source: bool) -> None:
    source = source.resolve()
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9, allowZip64=True) as archive:
        for path in _archive_files(source, clean_source=clean_source):
            # An output located below source must never be archived into itself.
            # Clean source builds normally exclude the whole release directory,
            # but this guard also makes the general helper safe by construction.
            if path.resolve() == output:
                continue
            relative = path.relative_to(source)
            name = Path(root_name) / relative
            # F22：时间戳不再硬编码——SOURCE_DATE_EPOCH（可复现构建）缺省回退文件 mtime
            stat = path.stat()
            source_epoch = os.environ.get("SOURCE_DATE_EPOCH")
            if source_epoch:
                try:
                    date_time = datetime.datetime.fromtimestamp(int(source_epoch), tz=datetime.UTC).timetuple()[:6]
                except (ValueError, OSError):
                    date_time = datetime.datetime.fromtimestamp(stat.st_mtime).timetuple()[:6]
            else:
                date_time = datetime.datetime.fromtimestamp(stat.st_mtime).timetuple()[:6]
            info = zipfile.ZipInfo(name.as_posix(), date_time=date_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.st_mode & 0xFFFF) << 16
            with path.open("rb") as handle, archive.open(info, "w", force_zip64=True) as target:
                while chunk := handle.read(1024 * 1024):
                    target.write(chunk)
        # F13：显式写目录条目——约定目录（data/input、work、output 等）若为空，
        # 解压后不会自动创建，用户首次运行看不到落点
        source_epoch = os.environ.get("SOURCE_DATE_EPOCH")
        for dir_path in sorted(p for p in source.rglob("*") if p.is_dir()):
            relative_dir = dir_path.relative_to(source)
            dir_name = (Path(root_name) / relative_dir).as_posix() + "/"
            if dir_name in archive.namelist():
                continue
            if source_epoch:
                try:
                    dir_date = datetime.datetime.fromtimestamp(int(source_epoch), tz=datetime.UTC).timetuple()[:6]
                except (ValueError, OSError):
                    dir_date = datetime.datetime.fromtimestamp(dir_path.stat().st_mtime).timetuple()[:6]
            else:
                dir_date = datetime.datetime.fromtimestamp(dir_path.stat().st_mtime).timetuple()[:6]
            dir_info = zipfile.ZipInfo(dir_name, date_time=dir_date)
            dir_info.external_attr = (0x10 << 16) | 0o40755  # 目录属性（MS-DOS dir bit）
            archive.writestr(dir_info, "")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--root-name", required=True)
    parser.add_argument("--clean-source", action="store_true")
    args = parser.parse_args()
    create_zip(args.source, args.output, args.root_name, args.clean_source)
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
