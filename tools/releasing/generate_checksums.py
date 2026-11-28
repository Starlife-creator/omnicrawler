"""Generate a sorted SHA-256 release manifest covering ALL release artifacts.

Expected artifacts (validated against actual files, aligned with
.github/workflows/release.yml):
- Windows Standard ZIP
- Windows Full ZIP
- CycloneDX SBOM JSON
- Provenance JSON

Usage:
    python tools/releasing/generate_checksums.py dist/
    python tools/releasing/generate_checksums.py dist/ --output SHA256SUMS-0.7.0.txt --verify
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

# Windows CI 控制台默认 charmap 编码无法输出中文/符号，强制 UTF-8。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

# 平台产物 → 该平台 job 应覆盖的发行物（不要求其他平台产物）
#: 每个平台应覆盖的发行物。
#:
#: ★ 2026-11-19 维护者裁定：**不再构建 Full**。此前这里列了 Standard + Full
#: 两件，于是只要 Full 不在就整步失败（真实 CI：`exit code 2`，
#: 日志报「以下类型的发行物未包含：...Linux-Portable-Full.tar.xz」）。
#: 现在只要求 Standard —— 它是唯一对外产物。
_PLATFORM_REQUIRED = {
    "Windows-Portable": ("Windows-Portable-Standard.zip",),
    "Linux-Portable": ("Linux-Portable-Standard.tar.xz",),
    "macOS-Portable": ("macOS-Portable-Standard.dmg",),
}


def _project_version() -> str:
    root = Path(__file__).resolve().parents[2]
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return str(metadata["project"]["version"])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _check_coverage(files: list[Path]) -> list[str]:
    """Verify required artifact categories are covered (case-insensitive:
    actual artifacts use lowercase names, e.g. ``omnicrawler-sbom.cdx.json``).

    按目录中实际包含的平台产物推导应覆盖项：只要求本平台的 Portable 双 edition
    产物（Linux/macOS job 不应因缺 Windows zip 而失败），通用项（平台 SBOM/
    provenance）按名称前缀接受（omnicrawler-sbom-<platform>.cdx.json /
    omnicrawler-provenance.json）。
    """
    filenames = " ".join(path.name for path in files).casefold()
    missing: list[str] = []

    # 平台产物：本目录含某个平台标识，则要求该平台的双 edition 产物都在。
    #
    # ★ 2026-11-28：这里原本有 `break` —— 命中**第一个**平台就不再查后面的。
    #   单平台目录（各平台构建 job）行为不变；但聚合后的发布目录同时含三平台，
    #   `break` 会让 Windows 的存在掩盖 Linux/macOS 的缺失 —— 一个静默假通过。
    #   现在逐个检查所有命中的平台。
    matched = 0
    for platform_marker, required in _PLATFORM_REQUIRED.items():
        if platform_marker.casefold() in filenames:
            matched += 1
            for pattern in required:
                if pattern.casefold() not in filenames:
                    missing.append(pattern)
    if matched == 0:
        missing.append("Portable-<Platform>-Standard.<ext>")

    # 通用元数据：SBOM 按前缀匹配（平台 job 产出带平台名的 SBOM，如
    # omnicrawler-sbom-macos.cdx.json）。provenance 由聚合 release job 单独
    # 生成并校验（generate_provenance.py），不在各平台 job 的目录内，故不要求。
    if not any("sbom" in name.casefold() and name.casefold().endswith(".cdx.json") for name in (p.name for p in files)):
        missing.append("SBOM.cdx.json")
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


def _example_artifact_name(files: list[Path], version: str) -> str:
    """挑一个**真实存在于本目录**的便携包名，作为核对示例（2026-11-28）。

    修前这里写死 `OmniCrawler-{version}-Windows-Portable-Standard.zip`，
    于是 `SHA256SUMS-linux.txt` 教 Linux 用户去哈希一个 Windows 包 ——
    照抄必然「文件不存在」。

    宁可退化成通用占位（`<你的包文件名>`），也不给一个必然失败的示例。
    """
    for path in files:
        name = path.name
        if "Portable" in name and name.lower().endswith((".zip", ".tar.xz", ".dmg")):
            return name
    return "<你的包文件名>"

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
            # ★ 2026-11-28：`SKIPPED-<platform>.txt` 是「哪些包因超限没发出去」的
            #   记账，不是可下载资产 —— 把它写进校验清单，用户会照着哈希一个
            #   自己都不该下、也下不到的文件。
            and not path.name.upper().startswith("SKIPPED-")
        ),
        key=lambda path: path.name.casefold(),
    )

    if not files:
        print(f"错误：目录中没有文件 — {directory}")
        return 1

    # 生成清单
    #
    # ★ 2026-11-28：核对示例此前**写死了 Windows zip 的文件名**，于是
    #   `SHA256SUMS-linux.txt` 里教用户去 Get-FileHash 一个 Windows 包 ——
    #   照抄必然「文件不存在」。现在按本目录里**真实存在**的那个便携包取值；
    #   目录里没有便携包（如仅做 CI 校验的临时目录）时退回通用写法。
    example = _example_artifact_name(files, version)
    header = [
        f"# OmniCrawler {version} SHA-256 校验清单",
        "# 生成时间：自动生成",
        f"# 覆盖文件：{len(files)} 个",
        "#",
        "# 核对方法（PowerShell）:",
        f"#   Get-FileHash .\\{example} -Algorithm SHA256",
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
