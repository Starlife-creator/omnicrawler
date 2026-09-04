from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from tools import checkout_market


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True,
        text=True, encoding="utf-8",
    ).stdout.strip()


@pytest.fixture
def upstream(tmp_path, monkeypatch):
    monkeypatch.delenv("MARKET_REPO_TOKEN", raising=False)
    repo = tmp_path / "upstream"
    repo.mkdir()
    git(repo, "init", "--quiet", "--initial-branch=main")
    git(repo, "config", "core.autocrlf", "false")
    hashes = []
    for content in (b"first\n", b"latest\n"):
        (repo / "signed.txt").write_bytes(content)
        git(repo, "add", "signed.txt")
        git(repo, "-c", "user.name=Checkout Test", "-c", "user.email=checkout@example.invalid", "commit", "--quiet", "-m", "fixture")
        hashes.append(git(repo, "rev-parse", "HEAD"))
    return repo, hashes


def test_pinned_checkout_keeps_exact_revision_and_lf_bytes(upstream, tmp_path, monkeypatch):
    repo, hashes = upstream
    global_config = tmp_path / "gitconfig"
    global_config.write_text("[core]\n autocrlf = true\n eol = crlf\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    destination = tmp_path / "market"
    actual = checkout_market.checkout(str(repo), destination, revision=hashes[0])
    assert actual == hashes[0] != hashes[1]
    assert (destination / "signed.txt").read_bytes() == b"first\n"
    assert git(destination, "config", "core.autocrlf") == "false"
    assert git(destination, "config", "core.eol") == "lf"
    assert git(destination, "remote", "get-url", "origin") == str(repo)
    detached = subprocess.run(["git", "symbolic-ref", "-q", "HEAD"], cwd=destination, capture_output=True)
    assert detached.returncode == 1


def test_latest_is_explicit_and_report_records_actual_sha(upstream, tmp_path, monkeypatch):
    repo, hashes = upstream
    report = tmp_path / "report.json"
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    code = checkout_market.main([
        "--repository", str(repo), "--destination", str(tmp_path / "latest"),
        "--latest", "--report", str(report),
    ])
    assert code == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["market_sha"] == hashes[1]
    assert payload["requested_revision"] == "refs/heads/main"
    assert payload["application_sha"] == git(checkout_market.ROOT, "rev-parse", "HEAD")
    assert hashes[1] in summary.read_text(encoding="utf-8")


def test_unavailable_pin_fails_instead_of_using_main(upstream, tmp_path):
    repo, _hashes = upstream
    destination = tmp_path / "unavailable"
    with pytest.raises(RuntimeError, match="git fetch failed"):
        checkout_market.checkout(str(repo), destination, revision="f" * 40)
    assert not (destination / "signed.txt").exists()


def test_existing_checkout_is_never_changed(upstream, tmp_path):
    repo, hashes = upstream
    destination = tmp_path / "existing"
    checkout_market.checkout(str(repo), destination, revision=hashes[0])
    (destination / "local.txt").write_text("unsaved work", encoding="utf-8")
    with pytest.raises(FileExistsError):
        checkout_market.checkout(str(repo), destination, revision=hashes[1])
    assert git(destination, "rev-parse", "HEAD") == hashes[0]
    assert (destination / "local.txt").read_text(encoding="utf-8") == "unsaved work"


@pytest.mark.parametrize("value", ["main", "1234567", "a" * 40 + "\n" + "b" * 40])
def test_pin_file_rejects_mutable_or_ambiguous_refs(tmp_path, value):
    pin = tmp_path / "pin.txt"
    pin.write_text(value, encoding="utf-8")
    with pytest.raises(ValueError, match="40-character"):
        checkout_market.read_pin(pin)


def test_invalid_pin_fails_before_creating_destination(tmp_path, capsys):
    pin = tmp_path / "pin.txt"
    pin.write_text("main", encoding="utf-8")
    destination = tmp_path / "market"
    assert checkout_market.main(["--ref-file", str(pin), "--destination", str(destination)]) == 1
    assert not destination.exists()
    assert "40-character" in capsys.readouterr().err


def test_token_only_enters_child_environment(monkeypatch, tmp_path):
    calls = []
    sha = "a" * 40
    token = "test-credential"
    encoded = base64.b64encode(f"x-access-token:{token}".encode()).decode()

    def fake_git(args, cwd, env):
        calls.append((args, dict(env)))
        return sha if args[0] == "rev-parse" else ""

    monkeypatch.setenv("MARKET_REPO_TOKEN", token)
    monkeypatch.setattr(checkout_market, "_git", fake_git)
    checkout_market.checkout(checkout_market.DEFAULT_REPOSITORY, tmp_path / "market", revision=sha, token=token)
    for args, env in calls:
        assert token not in str(args) and encoded not in str(args)
        assert "MARKET_REPO_TOKEN" not in env
        index = int(env["GIT_CONFIG_COUNT"]) - 1
        assert env[f"GIT_CONFIG_KEY_{index}"] == "http.https://github.com/.extraheader"
        assert env[f"GIT_CONFIG_VALUE_{index}"] == f"AUTHORIZATION: basic {encoded}"
    assert not any(args[0] == "config" and "extraheader" in str(args) for args, _env in calls)


def test_git_failure_does_not_echo_auth_diagnostics(monkeypatch, tmp_path):
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 128, stdout="", stderr="AUTHORIZATION: secret"),
    )
    with pytest.raises(RuntimeError) as error:
        checkout_market._git(["fetch", "origin", "main"], tmp_path, {})
    assert "secret" not in str(error.value)


def test_workflows_share_checkout_script_but_only_manual_job_uses_latest():
    workflow_root = checkout_market.ROOT / ".github" / "workflows"
    quality = yaml.load((workflow_root / "quality.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    for name in ("test", "windows-full-dependency-matrix"):
        steps = quality["jobs"][name]["steps"]
        market = [step for step in steps if "checkout_market.py" in step.get("run", "")]
        assert len(market) == 1
        assert market[0]["run"] == "python tools/checkout_market.py"
        assert "MARKET_REPO_TOKEN" in market[0]["env"]
        assert any("actions/setup-python@" in step.get("uses", "") for step in steps[:steps.index(market[0])])
    latest = yaml.load((workflow_root / "market-compatibility.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert set(latest["on"]) == {"workflow_dispatch"}
    steps = latest["jobs"]["latest-market"]["steps"]
    assert any("checkout_market.py --latest" in step.get("run", "") for step in steps)
    assert any("pytest tests/unit/plugin" in step.get("run", "") for step in steps)
    checkout_market.read_pin(checkout_market.ROOT / "constraints" / "market-ref.txt")
