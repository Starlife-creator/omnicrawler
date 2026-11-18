"""Repeatable local micro-benchmark for template discovery and HTML extraction."""

from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path

from omnicrawler.core.config import AppConfig
from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.extraction.extractors import HTMLProcessor
from omnicrawler.templates.template_catalog import bundled_template_catalog


def measure(callback, repeats: int) -> dict[str, float]:
    samples: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter()
        callback()
        samples.append((time.perf_counter() - started) * 1000)
    ordered = sorted(samples)
    return {
        "mean_ms": round(statistics.fmean(samples), 4),
        "median_ms": round(statistics.median(samples), 4),
        "p95_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 4),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=500)
    parser.add_argument("--output")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        raw = {
            "project": {"name": "benchmark", "workspace": str(root / "work")},
            "extract": {
                "mode": "html",
                "item_selector": "article",
                "fields": {
                    "title": {"selector": "h2", "transforms": ["normalize_space"]},
                    "url": {"selector": "a", "attr": "href"},
                    "price": {"selector": ".price", "regex": r"([0-9.]+)"},
                },
            },
        }
        config = AppConfig(root / "benchmark.yaml", root, raw, root / "work")
        processor = HTMLProcessor(config)
        body = (
            "<html><head><meta property='og:site_name' content='Bench'></head><body>"
            + "".join(
                f"<article><h2> Item {index} </h2><a href='/p/{index}'>view</a>"
                f"<span class='price'>¥{index}.50</span></article>"
                for index in range(100)
            )
            + "</body></html>"
        ).encode()
        result = FetchResult(
            CrawlRequest("https://example.invalid/list"),
            "https://example.invalid/list",
            200,
            {"content-type": "text/html; charset=utf-8"},
            body,
            0.01,
        )
        catalog = bundled_template_catalog()
        payload = {
            "repeats": args.repeats,
            "templates": len(catalog.discover()),
            "template_search": measure(lambda: catalog.search("API"), args.repeats),
            "html_extract_100_records": measure(lambda: processor.process(result), args.repeats),
            "note": "Informational baseline; CI does not use machine-dependent hard thresholds.",
        }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
