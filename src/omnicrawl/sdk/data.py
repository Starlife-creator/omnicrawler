"""Read-only dataset, attachment, evidence and quality query API."""

from __future__ import annotations

import csv
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ArtifactInfo:
    """Immutable metadata describing a single downloaded artifact.

    Attributes:
        name: File name without directory components.
        path: Absolute or workspace-relative path to the file.
        size_bytes: File size in bytes.
        kind: Category — ``"attachment"``, ``"evidence"``, or ``"output"``.
    """

    name: str
    path: str
    size_bytes: int
    kind: str


class DatasetReader:
    """Filesystem-independent, shaped result reader.

    Callers never receive DB handles or internal model objects — only
    plain dicts and :class:`ArtifactInfo` tuples.  The reader auto-detects
    output format (JSONL or CSV) and gracefully returns empty results
    when no output files exist.
    """

    def __init__(self, workspace: str | Path) -> None:
        """Initialise the reader for *workspace*.

        Args:
            workspace: Path to the run workspace directory (the folder
                that contains ``output/``, ``artifacts/``, ``raw/`` etc.).
        """
        self.workspace = Path(workspace).expanduser().resolve()

    def records(self) -> Iterator[dict[str, Any]]:
        """Yield extracted records as plain dicts.

        Auto-detects ``records.jsonl`` (preferred) or ``records.csv``.
        Yields nothing if neither file exists.
        """
        jsonl = next((path for path in (self.workspace / "output" / "records.jsonl", self.workspace / "records.jsonl") if path.is_file()), None)
        if jsonl:
            for line in jsonl.read_text(encoding="utf-8").splitlines():
                value = json.loads(line)
                if isinstance(value, dict):
                    yield value
            return
        csv_path = next((path for path in (self.workspace / "output" / "records.csv", self.workspace / "records.csv") if path.is_file()), None)
        if csv_path:
            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                yield from csv.DictReader(handle)

    def artifacts(self) -> tuple[ArtifactInfo, ...]:
        """Return all downloaded artifacts, sorted by (kind, path).

        Scans ``artifacts/``, ``raw/``, and ``output/`` directories.
        Returns an empty tuple if none exist.
        """
        result: list[ArtifactInfo] = []
        for directory, kind in (("artifacts", "attachment"), ("raw", "evidence"), ("output", "output")):
            root = self.workspace / directory
            if root.is_dir():
                result.extend(
                    ArtifactInfo(path.name, str(path), path.stat().st_size, kind)
                    for path in root.rglob("*") if path.is_file()
                )
        return tuple(sorted(result, key=lambda item: (item.kind, item.path)))

    def quality_report(self) -> dict[str, Any]:
        """Return the quality assessment report, or ``{"available": False}``.

        Reads ``output/quality_report.json`` if present; otherwise returns
        a dict indicating the report is unavailable.
        """
        path = self.workspace / "output" / "quality_report.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"available": False}
        return {"available": True, "report": value}
