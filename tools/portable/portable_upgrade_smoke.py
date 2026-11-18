"""Pre-release old-archive -> candidate upgrade check using real product CLIs.

The caller supplies an old portable archive, its candidate, and a **signed**
offline upgrade package.  This tool never creates an upgrade package, disables
signature verification, or claims success when either archive is absent.

The old installation is exercised in an isolated, non-default workspace with
configuration and plugin sentinels.  The candidate is applied through the
product's ``self-update apply --package ... --yes`` command, then runtime
integrity, the packaged smoke, preservation, and a loopback fixture-truth crawl
are checked.  In-place application checks that the upgraded executable reports
exactly the candidate archive's version.  ``--to-versions`` is rejected: the
current product's local-package path ignores that switch and applies in place.

This is a local release check: it downloads nothing.  ``--offline`` (the
default) records that contract; ``--require-offline`` additionally delegates
outbound-refusal probes to ``offline_acceptance.assert_offline``.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

_TOOLS_DIR = Path(__file__).resolve().parent
_ITEMS = ("Alpha", "Beta", "Gamma")


def _load(name: str) -> Any:
    path = _TOOLS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_portable_upgrade_{name}", path)
    if spec is None or spec.loader is None:  # pragma: no cover - missing sibling
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _archive() -> Any:
    return _load("portable_archive_smoke")


def _smoke() -> Any:
    return _load("portable_smoke_test")


def _executable(root: Path) -> Path:
    executable = _smoke()._resolve_executable(root)  # noqa: SLF001 - shared resolver
    if executable is None:
        raise FileNotFoundError(f"no packaged entry point in {root}")
    return executable


def _run(
    argv: list[str], cwd: Path, *, install_root: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    if install_root is not None:
        env["OMNICRAWL_INSTALL_ROOT"] = str(install_root.resolve())
    return subprocess.run(  # noqa: S603 - fixed product argv plus caller paths
        argv,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
        env=env,
    )


def _require_success(completed: subprocess.CompletedProcess[str], label: str) -> None:
    if completed.returncode != 0:
        output = (completed.stdout + "\n" + completed.stderr).strip()
        raise RuntimeError(f"{label} failed (rc={completed.returncode}):\n{output[-8000:]}")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _seed_state(install_root: Path) -> tuple[Path, dict[str, str]]:
    workspace = install_root / "data" / "customer-projects" / "local-truth"
    workspace.mkdir(parents=True, exist_ok=False)
    sentinels = {
        workspace / "legacy-task.json": b'{"name":"keep-old-task"}\n',
        install_root / "configs" / "customer.yaml": b"project:\n  name: keep-customer-settings\n",
        install_root / "plugins_installed" / "legacy" / "plugin.py": b"# keep-old-plugin\n",
    }
    for path, content in sentinels.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return workspace, {path.relative_to(install_root).as_posix(): _digest(path) for path in sentinels}


def _assert_preserved(install_root: Path, before: dict[str, str]) -> None:
    for relative, digest in before.items():
        path = install_root / relative
        if not path.is_file() or _digest(path) != digest:
            raise RuntimeError(f"upgrade did not preserve user-owned data: {relative}")


def _config(
    path: Path, workspace: Path, install_root: Path, url: str, trusted_public_key: str
) -> None:
    path.write_text(
        f"""project:
  name: portable-upgrade-local-truth
  workspace: {workspace.resolve().as_posix()}
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
self_update:
  feed_url: "{(install_root / '.offline-feed-disabled').as_posix()}"
  trusted_public_key: "{trusted_public_key}"
""",
        encoding="utf-8",
    )


def _apply(
    old_root: Path,
    config: Path,
    package: Path,
    install_root: Path,
    platform: str,
    edition: str,
    *,
    to_versions: bool,
) -> dict[str, Any]:
    argv = [
        str(_executable(old_root)), "self-update", "apply", "--config", str(config),
        "--package", str(package.resolve()), "--platform", platform, "--edition", edition,
        "--json", "--yes",
    ]
    if to_versions:
        argv.append("--to-versions")
    completed = _run(argv, old_root, install_root=install_root)
    _require_success(completed, "signed offline self-update")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("self-update did not return its JSON result") from exc
    if payload.get("status") != "applied" or not payload.get("staged_version"):
        raise RuntimeError(f"self-update did not report an applied version: {payload}")
    return payload


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - HTTP handler API
        if self.path == "/robots.txt":
            body, content_type = b"User-agent: *\nAllow: /\n", "text/plain"
        else:
            body = (
                "<html><body><ul>"
                + "".join(f'<li class="item"><a>{item}</a></li>' for item in _ITEMS)
                + "</ul></body></html>"
            ).encode()
            content_type = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return


def _assert_local_truth(release_root: Path, config: Path, workspace: Path, install_root: Path) -> int:
    completed = _run(
        [str(_executable(release_root)), "run", "--config", str(config)],
        release_root,
        install_root=install_root,
    )
    _require_success(completed, "post-upgrade local-truth crawl")
    record_paths = sorted(workspace.rglob("records.jsonl"))
    if len(record_paths) != 1:
        raise RuntimeError(f"expected one post-upgrade records file, found {len(record_paths)}")
    records = [
        json.loads(line)
        for line in record_paths[0].read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip()
    ]
    titles = {
        record["data"]["title"].strip()
        for record in records
        if isinstance(record.get("data"), dict)
        and isinstance(record["data"].get("title"), str)
    }
    if len(records) != len(_ITEMS) or titles != set(_ITEMS):
        raise RuntimeError(
            f"post-upgrade local truth mismatch: records={len(records)} titles={sorted(titles)}"
        )
    return len(records)


def _extract(archive: Path, into: Path) -> Path:
    _archive().extract(archive, into)
    return _archive().locate_release_root(into)


def _archive_path(directory: Path, explicit: Path | None, platform: str) -> Path:
    return explicit.resolve() if explicit is not None else _archive().find_archive(directory, platform)


def _reported_version(root: Path, install_root: Path) -> str:
    completed = _run([str(_executable(root)), "--version"], root, install_root=install_root)
    _require_success(completed, "version query")
    return completed.stdout.strip().removeprefix("omnicrawler ").strip()


def run(
    *,
    old_dir: Path,
    new_dir: Path,
    package: Path,
    trusted_public_key: str,
    platform: str,
    edition: str,
    old_archive: Path | None = None,
    new_archive: Path | None = None,
    to_versions: bool = False,
    offline: bool = True,
    require_offline: bool = False,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    if not package.is_file():
        raise FileNotFoundError(f"caller must supply a signed offline upgrade package: {package}")
    if not trusted_public_key.strip():
        raise ValueError("--trusted-public-key is required; signature verification is never bypassed")
    if to_versions:
        raise ValueError(
            "--to-versions is not supported by the product's local-package apply path; "
            "it would apply in place, so this tool cannot claim a versions-install rollback"
        )
    if not offline and require_offline:
        raise ValueError("--require-offline cannot be combined with --no-offline")
    if require_offline:
        _load("offline_acceptance").assert_offline()

    temporary: tempfile.TemporaryDirectory[str] | None = None
    if work_dir is None:
        temporary = tempfile.TemporaryDirectory(prefix="omnicrawler-upgrade-smoke-")
        work = Path(temporary.name)
    else:
        work = work_dir.resolve()
        work.mkdir(parents=True, exist_ok=False)
    try:
        old_path = _archive_path(old_dir, old_archive, platform)
        new_path = _archive_path(new_dir, new_archive, platform)
        print(f"old archive: {old_path.name}")
        print(f"candidate archive: {new_path.name}")
        old_root = _extract(old_path, work / "old")
        new_root = _extract(new_path, work / "candidate")
        install_root = work / "installed"
        shutil.copytree(old_root, install_root)

        workspace, sentinels = _seed_state(install_root)
        truth_workspace = workspace / "upgrade-local-truth-work"
        config = workspace / "upgrade-task.yaml"
        _config(config, truth_workspace, install_root, "http://127.0.0.1/", trusted_public_key)
        old_version = _reported_version(install_root, install_root)
        candidate_version = _reported_version(new_root, install_root)
        if old_version == candidate_version:
            raise RuntimeError("old and candidate archives report the same version")
        _archive().runtime_verify(install_root)
        _archive().runtime_verify(new_root)
        _smoke().run_smoke_test(install_root, edition)

        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/"
            _config(config, truth_workspace, install_root, url, trusted_public_key)
            applied = _apply(
                install_root, config, package, install_root, platform, edition,
                to_versions=to_versions,
            )
            version = str(applied["staged_version"])
            selected = install_root
            if version != candidate_version or _reported_version(selected, install_root) != candidate_version:
                raise RuntimeError("applied package does not match the candidate archive version")
            _archive().runtime_verify(selected)
            _smoke().run_smoke_test(selected, edition)
            _assert_preserved(install_root, sentinels)
            delivered = _assert_local_truth(selected, config, truth_workspace, install_root)
            _executable(new_root)

            # Product UpgradeManager.apply performs failure rollback internally;
            # a successful in-place smoke must not manufacture a rollback proof.
            rollback = "product-internal-on-failure-not-exercised-by-successful-smoke"
        finally:
            server.shutdown()
            server.server_close()
        return {
            "old_archive": old_path.name,
            "candidate_archive": new_path.name,
            "signed_package": package.name,
            "mode": "offline-package-in-place",
            "offline": "verified" if require_offline else "local-only-not-probed",
            "preservation": "ok",
            "post_upgrade_records": delivered,
            "rollback": rollback,
        }
    finally:
        if temporary is not None:
            temporary.cleanup()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--old-dir", type=Path, required=True)
    parser.add_argument("--new-dir", type=Path, required=True)
    parser.add_argument("--old-archive", type=Path)
    parser.add_argument("--new-archive", type=Path)
    parser.add_argument("--package", type=Path, required=True, help="caller-provided signed upgrade zip")
    parser.add_argument(
        "--trusted-public-key",
        required=True,
        help="public key trusted by the product for this package (base64 or hex:...)",
    )
    parser.add_argument("--platform", choices=["linux", "windows", "macos"], required=True)
    parser.add_argument("--edition", choices=["Standard", "Full"], default="Full")
    parser.add_argument("--to-versions", action="store_true")
    parser.add_argument("--offline", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--require-offline", action="store_true")
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args(argv)
    result = run(
        old_dir=args.old_dir, new_dir=args.new_dir, package=args.package,
        trusted_public_key=args.trusted_public_key, platform=args.platform, edition=args.edition,
        old_archive=args.old_archive, new_archive=args.new_archive, to_versions=args.to_versions, offline=args.offline,
        require_offline=args.require_offline, work_dir=args.work_dir,
    )
    payload = json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True)
    print(payload)
    if args.manifest:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(payload + "\n", encoding="utf-8")
    print("portable upgrade smoke: OK (caller-supplied real archives and signed package)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
