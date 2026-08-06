# Dependency constraints

`quality.txt` pins the tools used by OmniCrawler 0.4.0 CI and release validation. GitHub Actions
sets `PIP_CONSTRAINT` so build isolation and direct installations use the same
versions.

Runtime dependencies remain bounded in `pyproject.toml` because several extras
are platform-specific. Per-platform, hash-locked runtime manifests remain a
planned hardening item; portable components already use signed manifests and
file hashes.

When updating a pin:

1. test Python 3.10, 3.12 and 3.13 on Windows and Linux;
2. run the complete quality workflow;
3. update `docs/TEST_REPORT.md` and `CHANGELOG.md`;
4. regenerate the SBOM and release hashes.
