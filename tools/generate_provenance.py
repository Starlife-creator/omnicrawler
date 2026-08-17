"""Generate a release provenance record without overstating local evidence.

The record is intentionally conservative: a build remains an internal
candidate until it has an immutable source commit, tag, CI run URL and verified
signature evidence. This lets users distinguish integrity metadata from a
public-release trust claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_value(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, check=False, timeout=5
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _project_version(root: Path) -> str:
    match = re.search(r'^version\s*=\s*"([^"]+)"', (root / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    if not match:
        raise ValueError("pyproject.toml 中缺少 project.version")
    return match.group(1)


def _ci_url() -> str:
    server = os.environ.get("GITHUB_SERVER_URL", "").rstrip("/")
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    run_id = os.environ.get("GITHUB_RUN_ID", "").strip()
    return f"{server}/{repository}/actions/runs/{run_id}" if server and repository and run_id else ""


def build_provenance(
    root: Path,
    artifacts: list[Path],
    *,
    commit: str = "",
    tag: str = "",
    ci_url: str = "",
    signature_status: str = "unknown",
) -> dict[str, Any]:
    """Build a machine-readable record and classify its evidence conservatively."""

    source_commit = commit or os.environ.get("GITHUB_SHA", "") or _git_value(root, "rev-parse", "HEAD")
    ci_tag = os.environ.get("GITHUB_REF_NAME", "") if os.environ.get("GITHUB_REF_TYPE") == "tag" else ""
    source_tag = tag or ci_tag
    if not source_tag:
        source_tag = _git_value(root, "describe", "--exact-match", "--tags", "HEAD")
    workflow_url = ci_url or _ci_url()
    artifact_entries = [
        {"name": path.name, "path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": _sha256(path)}
        for path in sorted({path.resolve() for path in artifacts})
    ]
    missing: list[str] = []
    if not re.fullmatch(r"[0-9a-fA-F]{40}", source_commit):
        missing.append("immutable_source_commit")
    if not source_tag:
        missing.append("release_tag")
    if not workflow_url:
        missing.append("hosted_ci_run")
    if not artifact_entries:
        missing.append("artifacts")
    if signature_status != "verified":
        missing.append("verified_signature")
    return {
        "format": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project": {"name": "omnicrawler-platform", "version": _project_version(root)},
        "source": {"commit": source_commit or None, "tag": source_tag or None},
        "build": {"python": sys.version.split()[0], "platform": platform.platform()},
        "ci": {"run_url": workflow_url or None},
        "signature": {"status": signature_status},
        "artifacts": artifact_entries,
        "release": {
            "classification": "public_release_eligible" if not missing else "internal_candidate",
            "missing_evidence": missing,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--artifact", type=Path, action="append", default=[])
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--commit", default="")
    parser.add_argument("--tag", default="")
    parser.add_argument("--ci-url", default="")
    parser.add_argument("--signature-status", choices=["unknown", "verified"], default="unknown")
    parser.add_argument("--require-public", action="store_true", help="缺少正式发布证据时返回非零状态")
    args = parser.parse_args(argv)
    root = args.project_root.resolve()
    artifacts = [path.resolve() for path in args.artifact]
    if args.artifact_dir:
        artifact_dir = args.artifact_dir.resolve()
        if not artifact_dir.is_dir():
            raise FileNotFoundError(f"产物目录不存在: {artifact_dir}")
        artifacts.extend(path for path in artifact_dir.rglob("*") if path.is_file() and path != args.output.resolve())
    missing = [path for path in artifacts if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"产物不存在: {missing[0]}")
    report = build_provenance(
        root,
        artifacts,
        commit=args.commit,
        tag=args.tag,
        ci_url=args.ci_url,
        signature_status=args.signature_status,
    )
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0 if not args.require_public or report["release"]["classification"] == "public_release_eligible" else 1


if __name__ == "__main__":
    raise SystemExit(main())
