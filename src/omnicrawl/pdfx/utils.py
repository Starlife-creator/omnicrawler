from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@contextmanager
def atomic_output_path(path: Path, suffix: str | None = None) -> Iterator[Path]:
    """Yield a sibling temporary path and atomically publish it on success."""
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.stem}_",
        suffix=suffix if suffix is not None else path.suffix,
        dir=path.parent,
    )
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        yield temp_path
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    with atomic_output_path(path) as temp_path:
        temp_path.write_text(text, encoding=encoding)


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def iter_pdfs(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() == ".pdf":
            yield path.resolve()


def chunks(items: Iterable[Any], size: int) -> Iterator[list[Any]]:
    batch: list[Any] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def clean_text(text: str, *, compress_ws: bool = True) -> str:
    text = text.replace("\x00", "").replace("\u00a0", " ")
    if compress_ws:
        text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def retry(operation, attempts: int = 3, base_delay: float = 1.0):
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return operation()
        except Exception as exc:  # noqa: BLE001 - preserve provider errors
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(base_delay * (2 ** attempt))
    assert last_error is not None
    raise last_error


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.I)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        value = json.loads(stripped)
        if not isinstance(value, dict):
            raise ValueError("模型返回的 JSON 顶层必须是对象")
        return value
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("模型响应中没有可解析的 JSON 对象")
        value = json.loads(stripped[start:end + 1])
        if not isinstance(value, dict):
            raise ValueError("模型返回的 JSON 顶层必须是对象")
        return value

