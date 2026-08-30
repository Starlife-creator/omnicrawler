"""Tests for OmniCrawler-market/tools/scan_plugin.py — pre-publish security scanning."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCANNER = REPO_ROOT.parent / "OmniCrawler-market" / "tools" / "scan_plugin.py"

pytestmark = pytest.mark.skipif(
    not SCANNER.is_file(),
    reason="OmniCrawler-market 仓库未 clone（需与主仓库同级），跳过扫描器测试",
)

_UTF8_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}


def _run_scan(*plugin_dirs: str, manifest: str | None = None) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(SCANNER), "scan", *plugin_dirs]
    if manifest:
        cmd += ["--manifest", manifest]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=_UTF8_ENV)


def _make_plugin_dir(tmp_path: Path, name: str, *, files: dict[str, str]) -> Path:
    plugin_dir = tmp_path / name
    plugin_dir.mkdir(parents=True)
    for rel, content in files.items():
        path = plugin_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return plugin_dir


def test_clean_plugin_passes(tmp_path: Path) -> None:
    plugin = _make_plugin_dir(
        tmp_path,
        "clean",
        files={"plugin.py": "def register(registry):\n    registry.register_source('demo', object)\n"},
    )
    result = _run_scan(str(plugin))
    assert result.returncode == 0, result.stdout


def test_sensitive_files_detected(tmp_path: Path) -> None:
    plugin = _make_plugin_dir(
        tmp_path,
        "dirty",
        files={
            "plugin.py": "def register(registry): pass\n",
            ".env": "SECRET=value\n",
            "backup.key": "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSj\n",
            "credentials.json": '{"api_key": "sk-test12345678901234567890123456"}\n',
        },
    )
    result = _run_scan(str(plugin))
    assert result.returncode == 1
    assert ".env" in result.stdout
    assert "backup.key" in result.stdout
    assert "credentials.json" in result.stdout


def test_high_entropy_and_tokens_detected(tmp_path: Path) -> None:
    token = "AKIA" + "A" * 16  # AWS Access Key pattern
    plugin = _make_plugin_dir(
        tmp_path,
        "token_dirty",
        files={"plugin.py": f"SECRET_TOKEN = '{token}'\n"},
    )
    result = _run_scan(str(plugin))
    assert result.returncode == 1
    assert "AWS" in result.stdout


def test_secret_field_in_yaml_detected(tmp_path: Path) -> None:
    plugin = _make_plugin_dir(
        tmp_path,
        "field_dirty",
        files={
            "plugin.py": "def register(registry): pass\n",
            "config.yaml": "private_key: 'abcdef1234567890abcdef1234567890'\n",
        },
    )
    result = _run_scan(str(plugin))
    assert result.returncode == 1
    assert "私钥" in result.stdout


def test_allowlist_rejects_unlisted_files(tmp_path: Path) -> None:
    plugin = _make_plugin_dir(
        tmp_path,
        "allowlist",
        files={
            "plugin.py": "def register(registry): pass\n",
            "extra.txt": "不该打包的文件\n",
            "plugin.yaml": "id: demo\nfiles:\n  - plugin.py\n",
        },
    )
    result = _run_scan(str(plugin), manifest=str(plugin / "plugin.yaml"))
    assert result.returncode == 1
    assert "允许列表外文件" in result.stdout
    assert "extra.txt" in result.stdout


def test_allowlist_missing_files_field_warns_only(tmp_path: Path) -> None:
    plugin = _make_plugin_dir(
        tmp_path,
        "no_files_field",
        files={
            "plugin.py": "def register(registry): pass\n",
            "plugin.yaml": "id: demo\n",
        },
    )
    result = _run_scan(str(plugin), manifest=str(plugin / "plugin.yaml"))
    assert result.returncode == 0
    assert "files" in result.stdout
    assert "警告" in result.stdout


def test_scan_skips_sig_and_md_files(tmp_path: Path) -> None:
    plugin = _make_plugin_dir(
        tmp_path,
        "skip",
        files={
            "plugin.py": "def register(registry): pass\n",
            "plugin.py.sig": "t" * 64,  # 高熵二进制签名文件不应被内容扫描误报
            "listing.md": "普通说明文本\n",
        },
    )
    result = _run_scan(str(plugin))
    assert result.returncode == 0, result.stdout


def test_rescan_skips_generated_package_metadata(tmp_path: Path) -> None:
    digest = "04b06d3fa0a54174877ad2b5cbb26d195216be31889d936a995d5213f0d269c8"
    plugin = _make_plugin_dir(
        tmp_path,
        "resign",
        files={
            "plugin.py": "def handle(operation, payload): return {}\n",
            "package.manifest.json": (
                '{"creator_fingerprint":"4c3014804d85de2568f85e62a3048429",'
                f'"files":{{"plugin.py":"sha256:{digest}"}}}}\n'
            ),
            "submission.json": (
                '{"package_manifest_sha256":"'
                + digest
                + '"}\n'
            ),
        },
    )

    result = _run_scan(str(plugin))

    assert result.returncode == 0, result.stdout
