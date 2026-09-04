# Dependency constraints

`quality.txt` pins the tools used by OmniCrawler 0.12.0 CI and release validation. GitHub Actions
sets `PIP_CONSTRAINT` so build isolation and direct installations use the same
versions.

Runtime dependencies remain bounded in `pyproject.toml` because several extras
are platform-specific. Per-platform, hash-locked runtime manifests remain a
planned hardening item; portable components already use signed manifests and
file hashes.

## Market test snapshot

`market-ref.txt` contains one full commit SHA for the market repository. The
quality jobs use `tools/checkout_market.py` to fetch that exact commit into the
sibling `OmniCrawler-market` directory. A missing revision fails the job; it
never falls back to `main`. The script refuses an existing destination, so a
developer's checkout and local edits are not reset. Failed fresh checkouts are
left for inspection rather than recursively deleted.

The initial pin is `7912f85d72d2631a72f59e1b6c9ef5a383bec431`, the market snapshot
used by the local plugin regression (337 passed, 8 skipped). It is a compatibility
baseline, not a statement that this is the latest market version.

To update this pin, validate the candidate with the application's existing
`tests/unit/plugin` suite and record the application/market SHA pair. Fetching
from GitHub still requires network access and, for a private repository, the
optional `MARKET_REPO_TOKEN`. The token is passed through the child environment,
not placed in the remote URL or persisted in Git configuration.

The separate **market compatibility** workflow is manually dispatched and checks
the latest market `main` with the same plugin tests. It uploads the resolved SHA
pair and JUnit results. Use it when changing ecosystem contracts; this avoids
duplicating the full platform/dependency matrix. It does not modify the pin or
application runtime settings. Normal CI and forks use the canonical upstream
market repository; a deliberately different registry can be tested locally with
the script's `--repository` option.

When updating a pin:

1. test Python 3.10, 3.12 and 3.13 on Windows and Linux;
2. run the complete quality workflow;
3. update `docs/TEST_REPORT.md` and `CHANGELOG.md`;
4. regenerate the SBOM and release hashes.
