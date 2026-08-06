"""Offline demo workspace and tutorial task generator.

Modes:
1. Quick demo - one-click load example config and run
2. Full tutorial - 10 progressive steps through all capabilities
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class DemoWorkspace:
    root: Path
    index: Path
    api: Path
    login: Path
    changed: Path
    config: Path


def _build_report_pdf(path: Path) -> None:
    """S2.5.18：PyMuPDF 生成合法 PDF（文本层，可直抽文本）。"""
    import fitz

    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Offline Annual Report", fontsize=16)
    page.insert_text((72, 110), "Revenue: 1,200,000", fontsize=12)
    page.insert_text((72, 132), "Profits: 240,000", fontsize=12)
    page.insert_text((72, 154), "Published: 2024-03-01", fontsize=12)
    document.save(str(path))
    document.close()


def _build_scan_pdf(path: Path) -> None:
    """S2.5.18：渲染成纯位图页（无文字层）——OCR 演示路径真实可走通。"""
    import fitz

    document = fitz.open()
    scratch = document.new_page()
    scratch.insert_text((72, 72), "OCR DEMO 2024", fontsize=18)
    scratch.insert_text((72, 110), "Scan Date: 2024-03-01", fontsize=12)
    pixmap = scratch.get_pixmap(dpi=150)
    document.delete_page(0)
    page = document.new_page()
    page.insert_image(page.rect, pixmap=pixmap)
    document.save(str(path))
    document.close()


def create_demo_workspace(root: Path) -> DemoWorkspace:
    root.mkdir(parents=True, exist_ok=True)
    files = {
        'index.html': (
            '<!doctype html><meta charset=utf-8><title>Offline News</title>'
            '<h1>EV Policy Updates</h1><article><h2>Subsidy Notice</h2>'
            '<p>Offline sample content</p><a href="report.pdf">Download PDF</a>'
            '</article><script>document.body.dataset.dynamic="ready"</script>'
        ),
        'login.html': (
            '<!doctype html><meta charset=utf-8><h1>Login Sim</h1>'
            '<input aria-label="user"><input type=password aria-label="pass">'
        ),
        'changed.html': (
            '<!doctype html><meta charset=utf-8><h1>Same-URL v2</h1>'
            '<p>Amount adjusted from 1M to 1.2M</p>'
        ),
        'api.json': '{"items":[{"title":"API Item","status":"published"}],"next":null}',
    }
    for name, content in files.items():
        (root / name).write_text(content, encoding='utf-8')
    _build_report_pdf(root / 'report.pdf')
    _build_scan_pdf(root / 'scan.pdf')
    config = root / 'offline-demo.yaml'
    config.write_text(
        'config_version: 3\nproject:\n  name: offline_demo\n  workspace: work/offline_demo\n'
        'source:\n  kind: file\n  seeds:\n    - ' + repr((root / 'index.html').as_uri()) + '\n'
        'crawl:\n  max_pages: 5\n  same_host: true\nextract:\n  mode: auto\n  fields: {}\n'
        'download:\n  enabled: true\n  extensions: [\'.pdf\']\n'
        'processors:\n  pdf:\n    enabled: true\n    skip_ocr: false\n'
        'outputs:\n  jsonl: true\n  csv: true\n  xlsx: true\n',
        encoding='utf-8',
    )
    return DemoWorkspace(
        root, root / 'index.html', root / 'api.json',
        root / 'login.html', root / 'changed.html', config,
    )


@dataclass(slots=True)
class TutorialStep:
    number: int
    title: str
    description: str
    what_you_do: str
    what_happens: str
    key_takeaway: str
    config_template: dict[str, Any] = field(default_factory=dict)
    requires_network: bool = False
    category: str = 'core'


TUTORIAL_STEPS: list[TutorialStep] = [
    TutorialStep(1, 'Page Capture', 'Start from a URL and save page content.', 'Paste a URL, select Save, click run.', 'System visits, extracts, saves to workspace.', 'Any URL is a starting point. Encoding, redirects, errors handled.', category='core'),
    TutorialStep(2, 'Section Crawling', 'Auto-discover multi-page content in a section.', 'Enter section URL, select Collect entire section.', 'System finds articles and fetches each page.', 'Pagination: URL params, Load More, infinite scroll.', category='core'),
    TutorialStep(3, 'Field Extraction', 'Extract title, body, date, author, links.', 'Add columns in field design step. Visual picker available.', 'Each page becomes a table row of extracted content.', 'Recommended fields cover 90% of needs.', category='core'),
    TutorialStep(4, 'File Downloads', 'Auto-detect and download PDF, Office docs, images.', 'Check Enable downloads, configure extensions.', 'System downloads matching files, organized by source.', 'Uses Content-Type and file signatures, not just extensions.', category='file'),
    TutorialStep(5, 'PDF and OCR', 'Extract text, tables from PDFs. OCR for scans.', 'Enable PDF processing, set OCR to Auto.', 'Text PDFs extract directly. Scanned PDFs use Tesseract/PaddleOCR.', 'OCR only when needed. Modern PDFs have text layers.', category='file'),
    TutorialStep(6, 'Change Monitoring', 'Periodically check same-URL for content changes.', 'Enable Change monitoring. First run = baseline.', 'System tracks history per URL. Deletion needs multi-run confirm.', 'For policies, announcements, prices - ongoing tracking.', category='monitor'),
    TutorialStep(7, 'Scheduled Runs', 'Configure tasks to run on a recurring schedule.', 'Add config in scheduling dialog with interval.', 'Registered locally. Pairs with OS scheduler for auto-runs.', 'Local schedules only. Can require AC power.', category='schedule'),
    TutorialStep(8, 'Excel Export', 'Export to Excel, CSV, JSONL and more.', 'Select formats. Excel+JSONL combo recommended.', 'View tables, open files from results page after completion.', 'Excel for review, JSONL for archiving, Parquet for big data.', category='core'),
    TutorialStep(9, 'Result Review', 'Human-review quality. Flag low-confidence data.', 'In review panel, check completeness and scores.', 'Results saved per-record. Export pass/fix/reject.', 'Review is optional. Summary usually suffices.', category='review'),
    TutorialStep(10, 'Error Recovery', 'Resume from checkpoint. No data loss.', 'If interrupted, click Resume from checkpoint.', 'Every success page auto-saved. Failed pages for retry.', 'Checkpoint-resume is the norm. Overhead negligible.', category='core'),
]


def get_tutorial_map() -> dict[str, Any]:
    return {
        'title': 'OmniCrawler Complete Tutorial',
        'subtitle': '10 progressive steps through all capabilities',
        'steps': [
            {'number': s.number, 'title': s.title, 'description': s.description,
             'category': s.category, 'requires_network': s.requires_network}
            for s in TUTORIAL_STEPS
        ],
        'categories': {'core': 'Core', 'file': 'Files/OCR', 'monitor': 'Monitoring',
                       'schedule': 'Scheduling', 'review': 'Review'},
    }


def get_tutorial_step(step_number: int) -> TutorialStep | None:
    for step in TUTORIAL_STEPS:
        if step.number == step_number:
            return step
    return None


def create_tutorial_workspace(root: Path, step_number: int) -> DemoWorkspace | None:
    step = get_tutorial_step(step_number)
    if step is None:
        return None
    return create_demo_workspace(root)
