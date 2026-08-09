"""Generate a sorted SHA-256 release manifest covering ALL release artifacts.

Expected artifacts (validated against actual files):
- Windows Standard ZIP
- Windows Full ZIP
- Python Wheel
- Source ZIP
- CycloneDX SBOM JSON
- ChangeLog (1.1→2.1)
- Quick Start guide
- Release Report
- Test Report

Usage:
    python tools/generate_checksums.py dist/
    python tools/generate_checksums.py dist/ --output SHA256SUMS-2.1.0.txt --verify
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

# Windows CI 控制台默认 charmap 编码无法输出中文/符号，强制 UTF-8。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

# 必须覆盖的发行物文件名模式
REQUIRED_PATTERNS = [
    "Windows-Portable-Standard.zip",
    "Windows-Portable-Full.zip",
    "py3-none-any.whl",
    "Source.zip",
    "SBOM.cdx.json",
    "ChangeLog.md",
    "Quick-Start.md",
    "Release-Report.md",
    "Test-Report.md",
]


def _project_version() -> str:
    root = Path(__file__).resolve().parents[1]
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return str(metadata["project"]["version"])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _check_coverage(files: list[Path]) -> list[str]:
    """Verify all required artifact categories are covered."""
    filenames = " ".join(path.name for path in files)
    missing = []
    for pattern in REQUIRED_PATTERNS:
        if pattern not in filenames:
            missing.append(pattern)
    return missing


def verify_manifest(directory: Path, manifest: Path) -> list[str]:
    """Verify manifest paths and hashes without allowing traversal outside the release directory."""
    errors: list[str] = []
    root = directory.resolve()
    if not manifest.is_file():
        return [f"校验清单不存在: {manifest}"]
    seen: set[str] = set()
    for line_number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2 or len(parts[0]) != 64:
            errors.append(f"清单第{line_number}行格式无效")
            continue
        expected, filename = parts[0].casefold(), parts[1].strip().lstrip("*")
        relative = Path(filename)
        if relative.is_absolute() or ".." in relative.parts or relative.name != filename:
            errors.append(f"清单第{line_number}行包含不安全路径: {filename}")
            continue
        key = filename.casefold()
        if key in seen:
            errors.append(f"清单包含重复文件: {filename}")
            continue
        seen.add(key)
        target = (root / filename).resolve()
        if target.parent != root:
            errors.append(f"清单路径越界: {filename}")
        elif not target.is_file():
            errors.append(f"发行物不存在: {filename}")
        elif _sha256(target) != expected:
            errors.append(f"SHA-256不匹配: {filename}")
    if not seen:
        errors.append("校验清单没有任何文件条目")
    return errors


def main() -> int:
    version = _project_version()
    parser = argparse.ArgumentParser(
        description="Generate a complete SHA-256 release manifest with coverage validation"
    )
    parser.add_argument("directory", type=Path, help="Directory containing release artifacts")
    parser.add_argument("--output", default=f"SHA256SUMS-{version}.txt", help="Output manifest filename")
    parser.add_argument("--verify", action="store_true", help="Verify all required artifacts are covered")
    parser.add_argument("--check", action="store_true", help="Verify an existing manifest without rewriting it")
    args = parser.parse_args()

    directory = args.directory.resolve()
    if not directory.is_dir():
        print(f"错误：目录不存在 — {directory}")
        return 1

    output = directory / args.output
    if args.check:
        errors = verify_manifest(directory, output)
        if errors:
            for error in errors:
                print(f"错误：{error}")
            return 1
        print(f"OK: SHA-256 校验通过：{output}")
        return 0
    files = sorted(
        (
            path for path in directory.iterdir()
            if path.is_file()
            and path.resolve() != output.resolve()
            and not path.name.upper().startswith("SHA256SUMS")
        ),
        key=lambda path: path.name.casefold(),
    )

    if not files:
        print(f"错误：目录中没有文件 — {directory}")
        return 1

    # 生成清单
    header = [
        f"# OmniCrawler {version} SHA-256 校验清单",
        "# 生成时间：自动生成",
        f"# 覆盖文件：{len(files)} 个",
        "#",
        "# 核对方法（PowerShell）:",
        f"#   Get-FileHash .\\OmniCrawler-{version}-Windows-Portable-Standard.zip -Algorithm SHA256",
        "#   将结果与此文件对照",
        "",
    ]
    entries = [f"{_sha256(path)}  {path.name}" for path in files]
    output.write_text("\n".join(header + entries) + "\n", encoding="utf-8")
    print(f"清单已生成：{output}")
    print(f"覆盖文件：{len(files)} 个")

    # 覆盖率检查
    if args.verify:
        missing = _check_coverage(files)
        if missing:
            print("\n⚠ 覆盖率警告 — 以下类型的发行物未包含：")
            for pattern in missing:
                print(f"  - ...{pattern}")
            print("请确认这些是否应该包含在此次发布中。")
            return 2
        print("\n✓ 全部发行物类型已覆盖。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
