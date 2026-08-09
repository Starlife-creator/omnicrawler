from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a compact CycloneDX-compatible dependency SBOM")
    parser.add_argument("--output", default="dist/omnicrawler-sbom.cdx.json")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = (root / args.output).resolve()
    project = importlib.metadata.distribution("omnicrawl-platform")
    components: list[dict[str, Any]] = []
    for requirement in project.requires or []:
        name = re.split(r"[\s<>=!~;\[]", requirement, maxsplit=1)[0]
        try:
            distribution = importlib.metadata.distribution(name)
            version = distribution.version
            license_name = _license(distribution.metadata)
        except importlib.metadata.PackageNotFoundError:
            version = "not-installed-optional"
            license_name = "NOASSERTION"
        components.append({
            "type": "library",
            "name": name,
            "version": version,
            "licenses": [{"license": {"name": license_name}}],
            "purl": f"pkg:pypi/{name.lower()}@{version}",
            "properties": [{"name": "omnicrawler:requirement", "value": requirement}],
        })
    components.sort(key=lambda item: (item["name"].casefold(), item["version"]))
    serial_seed = f"omnicrawl-platform:{project.version}:{datetime.now(timezone.utc).date()}"
    document = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": "urn:uuid:" + _uuid_from_hash(serial_seed),
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "component": {"type": "application", "name": "omnicrawl-platform", "version": project.version},
        },
        "components": components,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output)


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


if __name__ == "__main__":
    main()

