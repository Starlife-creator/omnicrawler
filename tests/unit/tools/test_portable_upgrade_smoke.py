"""Boundary tests for the real-archive, signed-package upgrade smoke tool.

No test here executes an upgrade or stands in for an archive-based release
check.  Product processes, archive helpers, packaged smoke, and offline probes
are replaced at the tool boundary; only isolated fixture files are written.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_MODULE_PATH = Path(__file__).resolve().parents[3] / "tools" / "portable_upgrade_smoke.py"


def _module() -> Any:
    spec = importlib.util.spec_from_file_location("portable_upgrade_smoke", _MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "installed"
    root.mkdir()
    (root / "omnicrawler-cli.exe").write_bytes(b"executable placeholder")
    return root


def test_seeded_workspace_is_non_default_and_preservation_is_byte_exact(tmp_path: Path) -> None:
    module = _module()
    root = _root(tmp_path)
    workspace, sentinels = module._seed_state(root)

    assert workspace == root / "data" / "customer-projects" / "local-truth"
    assert set(sentinels) == {
        "configs/customer.yaml",
        "data/customer-projects/local-truth/legacy-task.json",
        "plugins_installed/legacy/plugin.py",
    }
    module._assert_preserved(root, sentinels)
    (root / "configs" / "customer.yaml").write_bytes(b"changed\n")
    with pytest.raises(RuntimeError, match="configs/customer.yaml"):
        module._assert_preserved(root, sentinels)


def test_apply_invokes_product_with_signed_package_and_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    root = _root(tmp_path)
    package = tmp_path / "signed.zip"
    package.write_bytes(b"not created or verified by this tool")
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_run(argv: list[str], cwd: Path, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, '{"status":"applied","staged_version":"2.0.0"}', "")

    monkeypatch.setattr(module, "_run", fake_run)
    result = module._apply(root, root / "task.yaml", package, root, "windows", "Full", to_versions=False)

    argv, kwargs = calls[0]
    assert result["staged_version"] == "2.0.0"
    assert argv[1:] == [
        "self-update", "apply", "--config", str(root / "task.yaml"),
        "--package", str(package), "--platform", "windows", "--edition", "Full",
        "--json", "--yes",
    ]
    assert "--to-versions" not in argv
    assert not any("verify" in item.lower() or "skip" in item.lower() for item in argv)
    assert kwargs["install_root"] == root


def test_apply_rejects_product_failure_and_non_applied_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    root = _root(tmp_path)
    monkeypatch.setattr(
        module, "_run", lambda *_args, **_kwargs: subprocess.CompletedProcess([], 2, "", "bad signature")
    )
    with pytest.raises(RuntimeError, match="bad signature"):
        module._apply(root, root / "task.yaml", tmp_path / "signed.zip", root, "linux", "Full", to_versions=False)
    monkeypatch.setattr(
        module, "_run", lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, '{"status":"planned"}', "")
    )
    with pytest.raises(RuntimeError, match="did not report an applied version"):
        module._apply(root, root / "task.yaml", tmp_path / "signed.zip", root, "linux", "Full", to_versions=False)


def test_local_truth_asserts_records_not_merely_process_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    root = _root(tmp_path)
    workspace = tmp_path / "work"

    def fake_run(argv: list[str], *_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        workspace.mkdir(exist_ok=True)
        records = [{"data": {"title": title}} for title in module._ITEMS]
        (workspace / "records.jsonl").write_text(
            "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
        )
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(module, "_run", fake_run)
    assert module._assert_local_truth(root, root / "task.yaml", workspace, root) == 3
    def fake_incorrect_run(argv: list[str], *_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        (workspace / "records.jsonl").write_text('{"data":{"title":"wrong"}}\n', encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(module, "_run", fake_incorrect_run)
    with pytest.raises(RuntimeError, match="local truth mismatch"):
        module._assert_local_truth(root, root / "task.yaml", workspace, root)


def test_run_rejects_unsigned_or_unsupported_invocations_before_work(tmp_path: Path) -> None:
    module = _module()
    package = tmp_path / "signed.zip"
    package.write_bytes(b"x")
    common = dict(
        old_dir=tmp_path, new_dir=tmp_path, package=package,
        trusted_public_key="public-key", platform="windows", edition="Full",
    )
    with pytest.raises(FileNotFoundError, match="caller must supply"):
        module.run(**{**common, "package": tmp_path / "absent.zip"})
    with pytest.raises(ValueError, match="signature verification is never bypassed"):
        module.run(**{**common, "trusted_public_key": " "})
    with pytest.raises(ValueError, match="local-package apply path"):
        module.run(**common, to_versions=True)
    with pytest.raises(ValueError, match="cannot be combined"):
        module.run(**common, offline=False, require_offline=True)


def test_require_offline_delegates_to_shared_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    package = tmp_path / "signed.zip"
    package.write_bytes(b"x")
    calls: list[str] = []
    monkeypatch.setattr(module, "_load", lambda name: SimpleNamespace(assert_offline=lambda: calls.append(name)))
    monkeypatch.setattr(module, "_archive_path", lambda *_args: (_ for _ in ()).throw(RuntimeError("stop after offline guard")))
    with pytest.raises(RuntimeError, match="stop after offline guard"):
        module.run(
            old_dir=tmp_path, new_dir=tmp_path, package=package, trusted_public_key="key",
            platform="windows", edition="Full", require_offline=True,
            work_dir=tmp_path / "work",
        )
    assert calls == ["offline_acceptance"]


def test_in_place_run_preserves_data_and_checks_candidate_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    package = tmp_path / "signed.zip"
    package.write_bytes(b"caller supplied")
    calls: list[str] = []
    archive = SimpleNamespace(
        find_archive=lambda directory, platform: directory / f"{platform}.zip",
        extract=lambda _archive, into: into.mkdir(parents=True),
        locate_release_root=lambda into: into,
        runtime_verify=lambda root: calls.append(f"verify:{root.name}"),
    )
    smoke = SimpleNamespace(
        _resolve_executable=lambda root, gui=False: root / "omnicrawler-cli.exe",
        run_smoke_test=lambda root, edition: calls.append(f"smoke:{root.name}:{edition}"),
    )
    monkeypatch.setattr(module, "_load", lambda name: {"portable_archive_smoke": archive, "portable_smoke_test": smoke}[name])
    versions = iter(["1.0.0", "2.0.0", "2.0.0"])
    monkeypatch.setattr(module, "_reported_version", lambda *_args: next(versions))

    def fake_apply(*_args: Any, **_kwargs: Any) -> dict[str, str]:
        calls.append("apply")
        return {"status": "applied", "staged_version": "2.0.0"}

    monkeypatch.setattr(module, "_apply", fake_apply)

    def fake_truth(root: Path, _config: Path, _workspace: Path, _install: Path) -> int:
        calls.append(f"truth:{root.name}")
        return 3

    monkeypatch.setattr(module, "_assert_local_truth", fake_truth)
    result = module.run(
        old_dir=tmp_path, new_dir=tmp_path, package=package, trusted_public_key="key",
        platform="windows", edition="Full", work_dir=tmp_path / "work",
    )
    assert result["mode"] == "offline-package-in-place"
    assert result["preservation"] == "ok"
    assert result["post_upgrade_records"] == 3
    assert result["rollback"].startswith("product-internal-on-failure")
    assert result["offline"] == "local-only-not-probed"
    assert calls == [
        "verify:installed", "verify:candidate", "smoke:installed:Full",
        "apply", "verify:installed", "smoke:installed:Full", "truth:installed",
    ]


def test_in_place_run_rejects_package_from_a_different_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    package = tmp_path / "signed.zip"
    package.write_bytes(b"caller supplied")
    archive = SimpleNamespace(
        find_archive=lambda directory, _platform: directory / "old.zip",
        extract=lambda _archive, into: into.mkdir(parents=True),
        locate_release_root=lambda into: into,
        runtime_verify=lambda _root: None,
    )
    smoke = SimpleNamespace(
        _resolve_executable=lambda root, gui=False: root / "omnicrawler-cli.exe",
        run_smoke_test=lambda _root, _edition: None,
    )
    monkeypatch.setattr(module, "_load", lambda name: {"portable_archive_smoke": archive, "portable_smoke_test": smoke}[name])
    versions = iter(["1.0.0", "2.0.0", "3.0.0"])
    monkeypatch.setattr(module, "_reported_version", lambda *_args: next(versions))
    monkeypatch.setattr(module, "_apply", lambda *_args, **_kwargs: {"status": "applied", "staged_version": "2.0.0"})
    monkeypatch.setattr(module, "_assert_local_truth", lambda *_args: 3)
    with pytest.raises(RuntimeError, match="does not match the candidate"):
        module.run(
            old_dir=tmp_path, new_dir=tmp_path, package=package, trusted_public_key="key",
            platform="windows", edition="Full", work_dir=tmp_path / "work",
        )
