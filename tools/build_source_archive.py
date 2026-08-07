"""Build the versioned clean source archive from pyproject metadata."""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

try:
    from tools.create_zip import create_zip
except ModuleNotFoundError:  # Direct execution adds tools/, not the project root, to sys.path.
    from create_zip import create_zip


def project_version(project_root: Path) -> str:
    metadata = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
    return str(metadata["project"]["version"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_root", nargs="?", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    args = parser.parse_args()
    root = args.project_root.resolve()
    version = project_version(root)
    output = args.output_dir.resolve() / f"OmniCrawler-{version}-Source.zip"
    create_zip(root, output, f"OmniCrawler-{version}-Source", clean_source=True)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
