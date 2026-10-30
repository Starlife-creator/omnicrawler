"""Read-only, non-executing audit of the selected paper plugin's host compatibility."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from omnicrawler.plugins.plugin_preflight import _decode_plugin_source, _preflight_forbidden_patterns


def _yaml_scalar(text: str, key: str) -> str | None:
    match = re.search(rf"^{re.escape(key)}:\s*(\S+)\s*$", text, re.MULTILINE)
    return match.group(1) if match else None


def resolve_payload(directory: Path) -> tuple[Path, Path]:
    """按 ``market.yaml`` 的权威指针解析当前版本的 plugin.py 与包清单。

    市场仓已迁到 ``versions/<v>/`` 布局，根目录仍留早期扁平载荷；硬编码根
    ``plugin.py`` 会审到遗留文件（实测：根 plugin.py 与 0.8.0 的 sha256 不同，
    根包清单还停在 0.3.0），而用户安装时下载的是目录里 ``plugin_file`` 指向的那份。
    无 market.yaml 时（例如直接指向某个版本目录）回退到扁平布局。
    """
    market_yaml = directory / "market.yaml"
    if market_yaml.is_file():
        text = market_yaml.read_text(encoding="utf-8")
        plugin_rel = _yaml_scalar(text, "plugin_file")
        manifest_rel = _yaml_scalar(text, "package_manifest_file")
        if plugin_rel and manifest_rel:
            repo_root = directory.parents[1]
            plugin_path = repo_root / plugin_rel
            manifest_path = repo_root / manifest_rel
            if plugin_path.is_file() and manifest_path.is_file():
                return plugin_path, manifest_path
    return directory / "plugin.py", directory / "package.manifest.json"


def audit(directory: Path) -> dict:
    path, manifest_path = resolve_payload(directory)
    payload = path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    source = _decode_plugin_source(path, payload)
    network, dangerous = _preflight_forbidden_patterns(path, source, set())
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = str(manifest.get("files", {}).get("plugin.py", "")).removeprefix("sha256:")
    return {"format": 1, "plugin": directory.name, "source_sha256": digest,
            "source_file": path.as_posix(), "manifest_version": manifest.get("version"),
            "manifest_source_matches": expected == digest,
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
