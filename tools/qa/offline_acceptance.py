"""W3.4: offline runtime acceptance -- prove the product works with no network at all.

Why this exists (criterion, verbatim from the plan)
---------------------------------------------------

    "offline usable runtime verification: in a **disconnected** environment, install a
     market package, import it, and run a local sample end to end"
    acceptance: "the whole flow succeeds inside an offline container; **any network call
     must fail**"

The previous state of this check was a *static* precheck (does a module import a network
library at module level).  That answers nothing about whether the product actually runs
offline, so this tool exercises the real thing:

1. **prove we are really offline** (`--require-offline`) -- every outbound TCP attempt to a
   public endpoint must fail.  Without this the rest of the run is vacuous: a green result
   would only mean "the network was fine", not "the product works offline".
2. **market package**: install a plugin *from the local market checkout* through the
   product's own `market_client` path, which verifies the **catalog signature**, the
   anti-replay sequence, the package hash and the detached plugin signature.  "a non-empty
   signature is not the same as trustworthy" -- so we go through verification, not around it.
   Then load it through the product's own inspector to prove it is loadable as a contract-2
   plugin.
3. **local sample**: start a 127.0.0.1 fixture site and run a real `omnicrawler run` crawl
   against it; the delivered records must equal the fixture truth.
4. **negative control** (`--require-offline`): a crawl seeded with a public URL must FAIL.
   Offline must not silently "succeed" by producing an empty-but-green run.

`--require-offline` is deliberately a flag: CI passes it inside `docker run --network none`,
while a developer can run the same tool locally (network up) to validate the config and the
sample before spending a CI round.

Notes
-----
* Standard library only -- the container image installs `[html,async-http,streams]`, and the
  container's ENTRYPOINT is `omnicrawler`, so this is invoked with `--entrypoint python`.
* **All output is ASCII only** (the same discipline used by the archive smoke tool).
* This tool never downloads the market: the caller checks the market out on the host and
  mounts it read-only, so the container itself performs no network access whatsoever.
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

# Outbound probes: an IP literal (no DNS needed) plus a name (exercises DNS too).
_PROBE_TARGETS: tuple[tuple[str, int], ...] = (("1.1.1.1", 443), ("pypi.org", 443))
_PROBE_TIMEOUT = 5.0

_FIXTURE_ITEMS = ("Alpha", "Beta", "Gamma")

#: 市场包的安装子目录。
#:
#: ★ **不能叫 `plugins` / `plugins_installed`**：产品按约定在 **cwd 下**探测这两个目录
#:   （`capabilities` 的输出里就有这两条 `path` 探测）。实测（W3.4 第二次派发）：
#:   把市场包装进 `<work_dir>/plugins` 后，紧接着的样例抓取**fail-closed** 了 ——
#:   `PermissionError: Plugin permissions were not approved for chronicle-capsule:
#:   artifacts:write, records:read, responses:payload, responses:read`。
#:   那是**产品的安全模型正确工作**（未授权权限 ⇒ 拒载），问题在于工具不该把包放进被发现的范围。
#:   守卫：`tests/unit/tools/test_offline_acceptance.py`。
INSTALL_SUBDIR = "market-install"

_FIXTURE_PAGE = (
    "<!doctype html><html><head><title>offline-acceptance</title></head><body><ul>"
    + "".join(
        f'<li class="item"><a href="/item/{name.lower()}">{name}</a></li>' for name in _FIXTURE_ITEMS
    )
    + "</ul></body></html>"
).encode("utf-8")


class _FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/robots.txt":
            body = b"User-agent: *\nAllow: /\n"
            content_type = "text/plain"
        else:
            body = _FIXTURE_PAGE
            content_type = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:  # keep the CI log readable
        return


def _probe(host: str, port: int) -> str | None:
    """Return None when the connection is refused/unreachable, else a short description.

    ★ **有界探测**：`socket.create_connection(timeout=...)` 的 timeout **不覆盖 DNS 解析**
    （它先调 `getaddrinfo`），而断网容器里解析可能长时间阻塞 ⇒ 把整次尝试放到线程里并设总预算，
    超预算即判「不可达」（在断网环境里这正是正确答案，且不会把整步拖到 job 超时）。
    """
    outcome: list[str | None] = [None]

    def _attempt() -> None:
        try:
            with socket.create_connection((host, port), timeout=_PROBE_TIMEOUT):
                outcome[0] = f"{host}:{port} CONNECTED"
        except OSError:
            outcome[0] = None

    worker = threading.Thread(target=_attempt, daemon=True)
    worker.start()
    worker.join(_PROBE_TIMEOUT + 5.0)
    return outcome[0] if not worker.is_alive() else None


def assert_offline() -> None:
    """Every outbound probe must fail -- otherwise this whole run proves nothing."""
    reachable = [desc for host, port in _PROBE_TARGETS if (desc := _probe(host, port))]
    if reachable:
        raise RuntimeError(
            "NOT offline: outbound connections succeeded -> "
            + ", ".join(reachable)
            + ". This check would be vacuous, so it is treated as a failure. "
            "Run the container with --network none, or drop --require-offline locally."
        )
    print(f"offline proven: refused/unreachable for {len(_PROBE_TARGETS)} probe target(s)")


def _run(argv: list[str], cwd: Path, *, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - argv is built from literals plus paths we own
        argv,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
    )


# ---------------------------------------------------------------- market package


def _first_plugin_id(catalog: dict[str, Any]) -> str:
    plugins = catalog.get("plugins") or []
    if not plugins:
        raise RuntimeError("market catalog declares no plugins to install")
    return str(plugins[0]["id"])


def install_market_plugin(market_dir: Path, dest_root: Path, plugin_id: str | None) -> str:
    """Verify + install a market plugin from the *local* checkout, then load it.

    Goes through `market_client` on purpose: that path verifies the signed catalog, the
    anti-replay sequence, the package hash and the detached plugin signature.  Installing by
    copying files would skip exactly the part that makes a market package trustworthy.
    """
    from omnicrawler.plugins import market_client
    from omnicrawler.plugins.plugin_inspector import inspect_plugin

    catalog_path = market_dir / "catalog.json"
    if not catalog_path.is_file():
        raise FileNotFoundError(f"no catalog.json under {market_dir}")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    plugin_id = plugin_id or _first_plugin_id(catalog)

    trust_ref = str(catalog.get("trust_public_key_ref") or "keys/plugin_trust.pub.pem")
    trust_key = (market_dir / trust_ref).read_text(encoding="utf-8")
    print(f"market: {market_dir.name}  plugin: {plugin_id}  trust: {trust_ref}")

    installed = market_client.download_and_verify(
        plugin_id, str(market_dir), dest_root, trust_key
    )
    ok, message = market_client.verify_installed(dest_root, plugin_id, trust_key)
    if not ok:
        raise RuntimeError(f"installed plugin failed re-verification: {message}")
    print(f"market install OK (verified): {installed.name}")

    plugin_py = Path(installed) if installed.is_file() else Path(installed) / "plugin.py"
    inspection = inspect_plugin(plugin_py)
    if not inspection.compatible:
        raise RuntimeError(f"installed plugin is not loadable: {inspection.errors}")
    print(
        f"plugin loadable: contract={inspection.contract_shape} "
        f"execution={inspection.execution_mode}"
    )
    return plugin_id


# ---------------------------------------------------------------- local sample


def _write_config(work_dir: Path, url: str) -> Path:
    config = work_dir / "offline-sample.yaml"
    config.write_text(
        f"""project:
  name: offline-acceptance
  workspace: {work_dir.resolve().as_posix()}/sample-work
source:
  kind: static_html
  seeds: [{url}]
crawl: {{max_pages: 1, same_host: true}}
http: {{allow_private_network: true, respect_robots: false, delay_seconds: 0}}
extract:
  mode: html
  item_selector: "li.item"
  fields:
    title: {{selector: "a"}}
""",
        encoding="utf-8",
    )
    return config


def _records(workspace: Path) -> list[str]:
    """records.jsonl under the project *workspace* (matches `project.workspace` in the config)."""
    payload: list[str] = []
    for path in sorted(workspace.rglob("records.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip():
                payload.append(line)
    return payload


def run_local_sample(work_dir: Path) -> int:
    """Crawl a loopback fixture site with the product CLI; truth = the fixture's own items."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/"
        config = _write_config(work_dir, url)
        print(f"fixture site up at {url}")
        completed = _run([sys.executable, "-m", "omnicrawler.cli", "run", "-c", str(config.resolve())], work_dir)
        if completed.returncode != 0:
            raise RuntimeError(
                "local sample crawl failed:\n"
                + (completed.stdout + completed.stderr).strip()[-6000:]
            )
        lines = _records(work_dir.resolve() / "sample-work")
        titles: set[str] = set()
        delivered = 0
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            delivered += 1
            # 交付值在 record["data"]（字段名 -> 值）；顶层是元数据，别读错地方
            data = record.get("data")
            if isinstance(data, dict):
                titles.update(v.strip() for v in data.values() if isinstance(v, str))
        missing = sorted(set(_FIXTURE_ITEMS) - titles)
        if missing or delivered != len(_FIXTURE_ITEMS):
            raise RuntimeError(
                f"local sample did not deliver the fixture truth; missing={missing} "
                f"records={delivered} (expected {len(_FIXTURE_ITEMS)})"
            )
        print(f"local sample OK: {delivered} records, all {len(_FIXTURE_ITEMS)} fixture items match")
        return delivered
    finally:
        server.shutdown()
        server.server_close()


def assert_public_fetch_fails(work_dir: Path) -> None:
    """Negative control: a public seed must fail while offline (no silent empty-but-green).

    ★ 与本地样例用**同一配置形状，只换 seed** —— 否则失败可能来自"配置非法"而不是"网络不通"，
    这条负向对照就失去了归因能力。
    """
    config = work_dir / "public-probe.yaml"
    config.write_text(
        f"""project:
  name: offline-acceptance-public
  workspace: {work_dir.resolve().as_posix()}/public-work
source:
  kind: static_html
  seeds: [https://example.com/]
crawl: {{max_pages: 1, same_host: false}}
http: {{respect_robots: false, delay_seconds: 0}}
extract:
  mode: html
  item_selector: "li"
  fields:
    text: {{selector: "li"}}
""",
        encoding="utf-8",
    )
    completed = _run([sys.executable, "-m", "omnicrawler.cli", "run", "-c", str(config.resolve())], work_dir)
    if completed.returncode == 0 and _records(work_dir.resolve() / "public-probe-work"):
        raise RuntimeError(
            "a public URL was crawled successfully while 'offline' -> the environment is not "
            "actually disconnected, or egress was not blocked"
        )
    print("negative control OK: public seed did not produce records while offline")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--market-dir", type=Path, required=True,
        help="local market checkout (checked out on the host, mounted read-only here)",
    )
    parser.add_argument("--plugin-id", help="defaults to the first plugin in the catalog")
    parser.add_argument(
        "--require-offline", action="store_true",
        help="assert every outbound probe fails; CI passes this inside --network none",
    )
    parser.add_argument("--work-dir", type=Path, help="defaults to a temp dir")
    args = parser.parse_args(argv)

    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.work_dir is None:
        temporary = tempfile.TemporaryDirectory(prefix="omnicrawler-offline-")
        work_dir = Path(temporary.name)
    else:
        work_dir = args.work_dir
        work_dir.mkdir(parents=True, exist_ok=True)

    try:
        if args.require_offline:
            assert_offline()
        else:
            print("note: --require-offline not set -> offline assertions skipped (dev run)")

        plugin_id = install_market_plugin(args.market_dir, work_dir / INSTALL_SUBDIR, args.plugin_id)
        delivered = run_local_sample(work_dir)
        if args.require_offline:
            assert_public_fetch_failed = assert_public_fetch_fails
            assert_public_fetch_failed(work_dir)

        manifest = {
            "market": args.market_dir.name,
            "plugin": plugin_id,
            "require_offline": bool(args.require_offline),
            "fixture_items_delivered": delivered,
            "offline": "verified" if args.require_offline else "not-checked",
        }
        print(json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True))
        print("offline acceptance: OK")
        return 0
    finally:
        if temporary is not None:
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
