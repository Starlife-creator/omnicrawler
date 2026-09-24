# Dependency constraints

`quality.txt` pins the tools used by OmniCrawler 0.14.0 CI and release validation. GitHub Actions
sets `PIP_CONSTRAINT` so build isolation and direct installations use the same
versions.

Portable builds use `uv sync --locked` with the edition-specific extras, so the
runtime and PyInstaller versions come from `uv.lock` on each target platform.
The uv executable itself and the bootstrap packaging tools are pinned in
`quality.txt`; portable components additionally use signed manifests and file
hashes.

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

★ **2026-09-22 推进到 `6642d9b26100738b35c1a6e1e370cbc73ca062ef`**（市场仓 `main`）：
市场侧把插件许可白名单按方向 B 收紧后，`test` 作业里的**跨仓双向相等守卫**
（`tests/unit/plugin/test_plugin_audit.py::test_allowlist_matches_market_gate`）比对的是
**本 pin 对应的市场源码**，不是市场仓 `main`。若 pin 仍停在旧快照，守卫会拿旧白名单
（含 AGPL/GPL）去比对新策略 ⇒ **只在 CI 判红**（本机用真实同级市场仓则绿）。
⇒ **凡是市场侧的「策略」改动（许可白名单、schema 约束等），推进本 pin 是必需步骤，不是可选项。**
内容类改动仍按上面的校验流程自行决定何时推进。

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

★ **2026-09-23 推进到 `db905fd907a501cd25f1f646f47f3559eb12f0ba`**（市场仓 `main`）：
随"模板去重只留市场 + plugins 快照收口"同步推进。`test` 作业的跨仓守卫
（`test_plugin_audit` 许可白名单 + `test_market_catalog_cross_repo` templates/plugins
逐项相等 + 模板维护者签名真实验签）比对的都是本 pin 对应的市场源码；
pin 落后于市场仓时，这些守卫只在 CI 判红（本机用真实同级市场仓则绿）。

★ **2026-09-24 推进到 `cff9eb76ff9dd2ef2e0be12ac9acf88cad4d6364`**（市场仓 `main`）：
随"12 个站点适配器迁入市场（1.0.0）"同步推进。`6d65e1b` 曾因 pin 未随快照同步推进
而 CI 红（跨仓守卫比对的还是 db905fd 的 3 模板 catalog）——**sync_snapshot 之后必须同步
推进 pin**，两者是同一收口动作的两半。
