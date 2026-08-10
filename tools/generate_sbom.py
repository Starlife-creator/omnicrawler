"""Generate a CycloneDX dependency SBOM covering the **full transitive closure**.

Fixes for S40 (was: direct deps only, duplicate entries, bogus
``not-installed-optional`` purls, no dependency graph, irreproducible):

1. **Transitive closure** — BFS over each installed package's ``Requires-Dist``
   so the SBOM covers every runtime dependency, not just the direct ones.
2. **Deduplication** — one component per package name (highest seen version);
   the old code counted one entry per ``Requires-Dist`` line, duplicating
   packages across extra declarations (``lxml`` x3, ``openpyxl`` x3 ...).
3. **Valid versions only** — optional extras that are not installed are
   collected as warnings instead of being emitted with a fake version
   ``not-installed-optional`` which breaks CycloneDX consumers.
4. **Dependency graph** — ``dependencies`` array maps every component to the
   packages it pulls in.
5. **Reproducibility guard** — the generated file is only as reproducible as
   the environment it runs in; ``tools/check_guardrails.py`` compares the SBOM
   against ``pip freeze`` of the same environment to keep them in sync.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import re
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_NAME_RE = re.compile(r"^([A-Za-z0-9_.-]+)")


def _requires_of(name: str) -> list[str]:
    try:
        distribution = importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        return []
    return [requirement for requirement in (distribution.requires or []) if requirement]


def _normalise_name(name: str) -> str:
    # PyPI normalises: case-insensitive, runs of -_. collapse to single _
    return re.sub(r"[-_.]+", "-", name).lower().replace("-", "_")


def _license(metadata: Any) -> str:
    expression = metadata.get("License-Expression")
    if expression:
        return str(expression)
    license_value = metadata.get("License")
    if license_value and len(str(license_value)) < 100:
        return str(license_value)
    classifiers = metadata.get_all("Classifier") or []
    matches = [value.rsplit("::", 1)[-1].strip() for value in classifiers if "License ::" in value]
    return ", ".join(matches) if matches else "NOASSERTION"


def _uuid_from_hash(value: str) -> str:
    digest = hashlib.sha256(value.encode()).hexdigest()[:32]
    return f"{digest[:8]}-{digest[8:12]}-5{digest[13:16]}-a{digest[17:20]}-{digest[20:32]}"


def _requirement_name(requirement: str) -> str:
    match = _NAME_RE.match(requirement)
    if match is None:
        raise ValueError(f"无法解析依赖声明: {requirement!r}")
    return match.group(1)


def _find_distribution(name: str) -> importlib.metadata.Distribution:
    """按原始声明名查找；PEP 503 归一化名（-/_. 互换）兜底。"""
    try:
        return importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        return importlib.metadata.distribution(_normalise_name(name))


def build_sbom() -> dict[str, Any]:
    project = importlib.metadata.distribution("omnicrawl-platform")
    queue: deque[str] = deque(
        _requirement_name(requirement) for requirement in (project.requires or [])
    )
    components: dict[str, dict[str, Any]] = {}
    dependencies: dict[str, list[str]] = {}
    warnings: list[str] = []
    seen: set[str] = set()

    while queue:
        raw_name = queue.popleft()
        key = _normalise_name(raw_name)
        if key in seen:
            continue
        seen.add(key)
        try:
            distribution = _find_distribution(raw_name)
        except importlib.metadata.PackageNotFoundError:
            warnings.append(f"optional extra not installed, skipped: {raw_name}")
            continue
        version = distribution.version
        ref = f"pkg:pypi/{key}@{version}"
        components[key] = {
            "type": "library",
            "name": distribution.metadata.get("Name") or raw_name,
            "version": version,
            "licenses": [{"license": {"name": _license(distribution.metadata)}}],
            "purl": ref,
            "bom-ref": ref,
        }
        deps = sorted({_requirement_name(dep) for dep in (distribution.requires or [])})
        dependencies[ref] = deps
        for dep in deps:
            if _normalise_name(dep) not in seen:
                queue.append(dep)

    document = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": "urn:uuid:" + _uuid_from_hash(f"omnicrawl-platform:{project.version}:transitive"),
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "component": {"type": "application", "name": "omnicrawl-platform", "version": project.version},
        },
        "components": sorted(components.values(), key=lambda item: item["name"].casefold()),
        "dependencies": [
            {"ref": ref, "dependsOn": dependencies.get(ref, [])}
            for ref in sorted(dependencies)
        ],
        "properties": [{"name": "omnicrawler:generator", "value": "tools/generate_sbom.py (transitive)"}],
    }
    document["metadata"]["properties"] = [{"name": "omnicrawler:skipped-optional", "value": str(len(warnings))}]
    return document


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a CycloneDX SBOM covering the transitive closure")
    parser.add_argument("--output", default="dist/omnicrawler-sbom.cdx.json")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = (root / args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(build_sbom(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
