# Third-party runtime notices

OmniCrawler is AGPL-3.0 licensed. The Windows full portable package also redistributes components
under their own licenses:

- Chromium / Chrome for Testing — Chromium/BSD and bundled component licenses.
- ChromeDriver — Chromium/BSD license family.
- Tesseract OCR — Apache License 2.0.
- `tessdata_fast` language models — Apache License 2.0.
- PaddlePaddle, PaddleOCR and PaddleX — Apache License 2.0.
- Crawl4AI — Apache License 2.0. Used as an optional AI-driven crawling backend
  (`omnicrawler[crawl4ai]`).
- Python packages listed in `SBOM.json` — each package's declared license applies.

Bundled static assets redistributed inside this repository (see `docs/archive/`):

- `mermaid.min.js` (docs/archive/omnicrawler-evaluation-report/_shared/js/) — MIT License.
  Upstream: https://github.com/mermaid-js/mermaid
- `echarts.min.js` (docs/archive/omnicrawler-evaluation-report/_shared/js/) — Apache License 2.0.
  Upstream: https://github.com/apache/echarts
- Instrument Sans fonts (`_shared/fonts/InstrumentSans-*.ttf`) — SIL Open Font License 1.1.
  Upstream: https://github.com/googlefonts/instrument-sans

7-Zip is used only during the build to extract the Tesseract NSIS package and is not included
in the portable application. 7-Zip is licensed mainly under GNU LGPL with additional components;
source and license information: https://www.7-zip.org/

Project source links and exact installed versions are recorded in the release SBOM and runtime
manifest. Redistribution does not imply endorsement by the upstream projects.
