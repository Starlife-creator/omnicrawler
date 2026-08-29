# Third-party runtime notices

OmniCrawler is Apache-2.0 licensed. The project migrated from AGPL-3.0 after removing
all strong-copyleft runtime dependencies. The Windows full
portable package also redistributes components under their own licenses:

- Chromium / Chrome for Testing — Chromium/BSD and bundled component licenses.
- ChromeDriver — Chromium/BSD license family.
- Tesseract OCR — Apache License 2.0.
- `tessdata_fast` language models — Apache License 2.0.
- PaddlePaddle, PaddleOCR and PaddleX — Apache License 2.0.
- Crawl4AI — Apache License 2.0. Used as an optional AI-driven crawling backend
  (`omnicrawler[crawl4ai]`).
- Qt 6 (via PySide6 / shiboken6) — LGPL-3.0. Used for the desktop GUI and linked
  dynamically; Qt is not modified. Users may relink against a modified Qt build as
  permitted by the LGPL. Upstream: https://www.qt.io/ — license text at
  https://www.gnu.org/licenses/lgpl-3.0.txt
- pdfplumber / pdfminer.six / pypdf / reportlab / pypdfium2 — MIT / BSD-3-Clause /
  BSD-3-Clause / BSD / BSD-3-Clause+Apache-2.0 respectively. This PDF parsing and rendering
  stack replaced the former AGPL-licensed PyMuPDF dependency.
- Python packages listed in `SBOM.json` — each package's declared license applies.

7-Zip is used only during the build to extract the Tesseract NSIS package and is not included
in the portable application. 7-Zip is licensed mainly under GNU LGPL with additional components;
source and license information: https://www.7-zip.org/

Project source links and exact installed versions are recorded in the release SBOM and runtime
manifest. Redistribution does not imply endorsement by the upstream projects.
