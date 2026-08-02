# Third-party runtime notices

OmniCrawler is MIT licensed. The Windows full portable package also redistributes components
under their own licenses:

- Chromium / Chrome for Testing — Chromium/BSD and bundled component licenses.
- ChromeDriver — Chromium/BSD license family.
- Tesseract OCR — Apache License 2.0.
- `tessdata_fast` language models — Apache License 2.0.
- PaddlePaddle, PaddleOCR and PaddleX — Apache License 2.0.
- Python packages listed in `SBOM.json` — each package's declared license applies.

7-Zip is used only during the build to extract the Tesseract NSIS package and is not included
in the portable application. 7-Zip is licensed mainly under GNU LGPL with additional components;
source and license information: https://www.7-zip.org/

Project source links and exact installed versions are recorded in the release SBOM and runtime
manifest. Redistribution does not imply endorsement by the upstream projects.
