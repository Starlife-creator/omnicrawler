"""Read-only disk attribution; runtime metrics require their own measured evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
from pathlib import Path
from typing import Any


def inventory(directory: Path, *, label: str) -> dict[str, Any]:
    root = directory.resolve(strict=True)
    if not root.is_dir() or directory.is_symlink():
        raise ValueError("inventory requires a real directory")
    files: list[dict[str, Any]] = []
    skipped: list[str] = []
    groups: dict[str, int] = {}
    for current, directories, names in os.walk(root, followlinks=False):
        parent = Path(current)
        for name in list(directories):
            candidate = parent / name
            if candidate.is_symlink() or candidate.is_junction():
                skipped.append(candidate.relative_to(root).as_posix())
                directories.remove(name)
        for name in sorted(names):
            path = parent / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                skipped.append(relative)
                continue
            size = path.stat().st_size
            files.append({"path": relative, "bytes": size})
            group = path.relative_to(root).parts[0]
            groups[group] = groups.get(group, 0) + size
    if not files:
        raise ValueError("empty inventory cannot establish a baseline")
    files.sort(key=lambda item: item["path"])
    digest = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "format": 1, "label": label, "kind": "directory-metadata-inventory",
        "environment": {"platform": platform.platform(), "python": platform.python_version()},
        "disk_bytes": sum(item["bytes"] for item in files),
        "metadata_sha256": digest, "content_hash_verified": False,
        "groups": dict(sorted(groups.items(), key=lambda item: (-item[1], item[0]))),
        "files": files, "skipped_links": sorted(skipped),
        "unknown_metrics": ["compressed_artifact_bytes", "frozen_cold_start_ms",
                            "first_qualified_record_ms", "process_tree_peak_bytes",
                            "browser_launch_count", "effective_throughput", "ground_truth_quality"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = inventory(args.directory, label=args.label)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("label", "disk_bytes", "metadata_sha256", "unknown_metrics")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
