"""Validate nonempty scenario cards and local evidence hashes without executing packages."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from omnicrawler.services.scenario_cards import validate_card


def check(paths: list[Path], evidence_root: Path) -> list[dict]:
    if not paths:
        raise ValueError("zero scenario cards selected")
    root = evidence_root.resolve()
    results = []
    for path in paths:
        card = validate_card(json.loads(path.read_text(encoding="utf-8")))
        for evidence in card.get("evidence", []):
            target = (root / evidence["path"]).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                raise ValueError("evidence must be a file inside the evidence root")
            if hashlib.sha256(target.read_bytes()).hexdigest() != evidence["sha256"]:
                raise ValueError("evidence hash mismatch")
        results.append({"path": str(path), "verification": card["verification"]})
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cards", nargs="+", type=Path)
    parser.add_argument("--evidence-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(check(args.cards, args.evidence_root), ensure_ascii=False, indent=2))
    except (ValueError, OSError, TypeError) as exc:
        parser.exit(1, str(exc) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
