"""Compare widgets with a pinned source revision in the same Qt/font environment."""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_snapshot(source: Path, output: Path, *, generate: bool) -> dict[str, int]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(source)
    env["OMNI_BASELINE_DIR"] = str(output / "baselines")
    env["PYTHONUTF8"] = "1"
    env["OMNICRAWL_SKIP_FIRST_LAUNCH"] = "1"
    env.pop("OMNI_BASELINE", None)
    if generate:
        env["OMNI_BASELINE"] = "1"
    name = "reference" if generate else "current"
    xml = output / f"{name}.xml"
    code = (
        "import pathlib,sys,omnicrawler,pytest; "
        "assert pathlib.Path(omnicrawler.__file__).resolve().is_relative_to(pathlib.Path(sys.argv[1]).resolve()); "
        "raise SystemExit(pytest.main(sys.argv[2:]))"
    )
    command = [sys.executable, "-c", code, str(source), "-q", str(ROOT / "tests/gui/visual/test_snapshots.py"), f"--junitxml={xml}"]
    with (output / f"{name}.log").open("w", encoding="utf8") as log:
        result = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=180, check=False)
    if not xml.is_file():
        raise RuntimeError(f"{name}: missing test result; inspect {name}.log")
    suites = list(ET.parse(xml).getroot().iter("testsuite"))
    counts = {key: sum(int(s.get(key, 0)) for s in suites) for key in ("tests", "failures", "errors", "skipped")}
    if result.returncode or not counts["tests"] or any(counts[k] for k in ("failures", "errors", "skipped")):
        raise RuntimeError(f"{name}: visual regression failed: {counts}; inspect {name}.log")
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New evidence directory; existing directories are refused")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    reference = (ROOT / "constraints/visual-ref.txt").read_text(encoding="utf8").strip()
    if re.fullmatch(r"[0-9a-f]{40}", reference) is None:
        raise ValueError("Visual reference must be a full reviewed commit SHA")
    archive = subprocess.check_output(["git", "archive", reference, "src"], cwd=ROOT)
    baseline_source = output / "reference-source"
    baseline_source.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        bundle.extractall(baseline_source, filter="data")
    baseline = run_snapshot(baseline_source / "src", output, generate=True)
    current = run_snapshot(ROOT / "src", output, generate=False)
    images = list((output / "baselines").rglob("*.png"))
    if not images:
        raise RuntimeError("No visual reference images generated")
    report = {"reference_sha": reference, "source_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(), "reference": baseline, "current": current, "images": len(images), "qt_platform": os.environ.get("QT_QPA_PLATFORM", "native"), "status": "passed"}
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
