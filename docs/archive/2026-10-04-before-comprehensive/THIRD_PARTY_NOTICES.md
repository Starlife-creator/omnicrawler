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

Bundled static assets redistributed inside this repository (see `docs/archive/`):

- `mermaid.min.js` (docs/archive/omnicrawler-evaluation-report/_shared/js/) — MIT License.
  Upstream: https://github.com/mermaid-js/mermaid
  The bundle ships **without a top-level license banner**, so the notices + permission texts of
  mermaid and of every dependency bundled into it are redistributed alongside it in
  `_shared/js/LICENSES.txt` (W6.1, 2026-09-15). Those texts and copyright lines are copied
  verbatim from upstream releases; the component list is derived from identifiers found in the
  **bundle itself**, not from a manifest. Attribution is not guessed: zrender turned out to be
  BSD-3-Clause, d3 ISC, dompurify `Apache-2.0 OR MPL-2.0`.
- `echarts.min.js` (docs/archive/omnicrawler-evaluation-report/_shared/js/) — Apache License 2.0.
  Upstream: https://github.com/apache/echarts (bundles `zrender`, BSD 3-Clause).
  License texts are in `_shared/js/LICENSES.txt`, guarded by
  `tests/unit/tools/test_bundled_js_notices.py`.
- Fonts in `docs/archive/omnicrawler-evaluation-report/_shared/fonts/` — all three families are
  licensed under the **SIL Open Font License 1.1**; the license text (with each family's
  copyright notice) is redistributed alongside them in `_shared/fonts/OFL.txt`:
  - Instrument Sans (`InstrumentSans-*.ttf`) — Copyright 2022 The Instrument Sans Project Authors.
    Upstream: https://github.com/googlefonts/instrument-sans
  - JetBrains Mono (`JetBrainsMono-*.ttf`) — Copyright 2020 The JetBrains Mono Project Authors.
    Upstream: https://github.com/JetBrains/JetBrainsMono
  - Outfit (`Outfit-*.ttf`) — Copyright 2021 The Outfit Project Authors.
    Upstream: https://github.com/Outfitio/Outfit-Fonts

Branding assets redistributed in this repository (`assets/branding/`, and the runtime copy in
`src/omnicrawler/gui/branding/`):

- Outfit (Bold) — SIL Open Font License 1.1. Used only as a source for the lettering outlines
  embedded in the OmniCrawler wordmark; no font software is redistributed and no reserved font
  name is claimed. See `assets/branding/LICENSE-OFL.txt` and `assets/branding/FONT-NOTICE.txt`.
  (`license-gate.yml` cannot see this one: it scans the Python dependency closure only.)

7-Zip is used only during the build to extract the Tesseract NSIS package and is not included
in the portable application. 7-Zip is licensed mainly under GNU LGPL with additional components;
source and license information: https://www.7-zip.org/

Project source links and exact installed versions are recorded in the release SBOM and runtime
manifest. Redistribution does not imply endorsement by the upstream projects.
