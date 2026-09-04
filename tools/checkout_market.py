"""Check out the CI market snapshot without changing an existing checkout."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPOSITORY = "https://github.com/Starlife-creator/OmniCrawler-market.git"


def read_pin(path: Path) -> str:
    revision = path.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
        raise ValueError("Market pin must contain one full 40-character commit SHA")
    return revision.lower()


def _git(args: list[str], cwd: Path, env: dict[str, str]) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180,
    )
    if result.returncode:
        # Do not echo remote diagnostics or environment-derived authentication.
        raise RuntimeError(f"git {args[0]} failed (exit {result.returncode}); check repository access and revision")
    return result.stdout.strip()


def checkout(repository: str, destination: Path, *, revision: str, token: str = "") -> str:
    if revision != "refs/heads/main" and not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Expected a full commit SHA or the explicit latest main ref")
    url = urlsplit(repository)
    if url.username or url.password:
        raise ValueError("Repository URLs must not contain credentials")
    if token and (url.scheme != "https" or url.hostname != "github.com"):
        raise ValueError("Market token is restricted to HTTPS GitHub repositories")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"Refusing to overwrite an existing market checkout: {destination}")

    env = dict(os.environ)
    env.pop("MARKET_REPO_TOKEN", None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    if token:
        index = int(env.get("GIT_CONFIG_COUNT", "0"))
        env["GIT_CONFIG_COUNT"] = str(index + 1)
        env[f"GIT_CONFIG_KEY_{index}"] = "http.https://github.com/.extraheader"
        encoded = base64.b64encode(f"x-access-token:{token}".encode()).decode("ascii")
        env[f"GIT_CONFIG_VALUE_{index}"] = f"AUTHORIZATION: basic {encoded}"

    destination.mkdir(parents=True)
    _git(["init", "--quiet"], destination, env)
    # Signature fixtures must retain LF even with a user's global Windows config.
    _git(["config", "core.autocrlf", "false"], destination, env)
    _git(["config", "core.eol", "lf"], destination, env)
    _git(["remote", "add", "origin", repository], destination, env)
    _git(["fetch", "--quiet", "--no-tags", "--depth", "1", "origin", revision], destination, env)
    fetched = _git(["rev-parse", "FETCH_HEAD^{commit}"], destination, env)
    expected = fetched if revision == "refs/heads/main" else revision
    if fetched != expected:
        raise RuntimeError(f"Fetched market SHA differs from requested SHA: {fetched} != {expected}")
    _git(["checkout", "--quiet", "--detach", fetched], destination, env)
    actual = _git(["rev-parse", "HEAD"], destination, env)
    if actual != expected:
        raise RuntimeError(f"Checked out market SHA differs from requested SHA: {actual} != {expected}")
    return actual


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    parser.add_argument("--destination", type=Path, default=ROOT.parent / "OmniCrawler-market")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--ref-file", type=Path, default=ROOT / "constraints" / "market-ref.txt")
    mode.add_argument("--latest", action="store_true", help="Explicit compatibility check against main")
    parser.add_argument("--report", type=Path, help="Write the application/market SHA pair as JSON")
    args = parser.parse_args(argv)
    try:
        revision = "refs/heads/main" if args.latest else read_pin(args.ref_file)
        actual = checkout(
            args.repository, args.destination.absolute(), revision=revision,
            token=os.environ.get("MARKET_REPO_TOKEN", ""),
        )
        report = {
            "application_sha": _git(["rev-parse", "HEAD"], ROOT, dict(os.environ)),
            "market_repository": args.repository,
            "requested_revision": revision,
            "market_sha": actual,
        }
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with Path(summary).open("a", encoding="utf-8") as handle:
                handle.write(f"\nApplication SHA: `{report['application_sha']}`\n\nMarket SHA: `{actual}`\n")
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"Market checkout failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
