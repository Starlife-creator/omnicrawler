"""Read-only, non-executing audit of the selected paper plugin's host compatibility."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from omnicrawler.plugins.plugin_preflight import _decode_plugin_source, _preflight_forbidden_patterns


def audit(directory: Path) -> dict:
    path = directory / "plugin.py"
    payload = path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    source = _decode_plugin_source(path, payload)
    network, dangerous = _preflight_forbidden_patterns(path, source, set())
    manifest = json.loads((directory / "package.manifest.json").read_text(encoding="utf-8"))
    expected = str(manifest.get("files", {}).get("plugin.py", "")).removeprefix("sha256:")
    return {"format": 1, "plugin": directory.name, "source_sha256": digest,
            "manifest_version": manifest.get("version"), "manifest_source_matches": expected == digest,
            "direct_network_modules": sorted(network), "dangerous_patterns": sorted(dangerous),
            "decision": "stop_plugin_route" if network or dangerous or expected != digest else "requires_runtime_acceptance",
            "executed_plugin": False, "signature_verification": "not_performed",
            "next_action": "SDK networking and scoped session migration, new signed version, then runtime acceptance"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    report = audit(args.directory)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
