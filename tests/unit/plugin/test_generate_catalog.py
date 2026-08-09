"""Tests for tools/generate_catalog.py — git-as-registry catalog generation/checks."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from omnicrawl.plugins import signing  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
GENERATOR = REPO_ROOT / "registry" / "tools" / "generate_catalog.py"
REAL_REGISTRY = REPO_ROOT / "registry"
TRUST_ROOT = REPO_ROOT / "configs" / "plugin_trust.pub.pem"

_UTF8_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GENERATOR), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_UTF8_ENV,
    )


def _build_registry(tmp_path: Path) -> tuple[Path, Path, bytes]:
    """构造最小 registry：一个签名插件 + 一个作者记录。返回 (registry, trust_pem)。"""
    private_pem, public_pem = signing.generate_keypair()
    registry = tmp_path / "registry"
    plugin_dir = registry / "plugins" / "demo_plug"
    plugin_dir.mkdir(parents=True)
    trust_pem = tmp_path / "trust.pub.pem"
    trust_pem.write_bytes(public_pem)

    fingerprint = hashlib.sha256(public_pem).hexdigest()[:32]

    plugin_path = plugin_dir / "plugin.py"
    plugin_path.write_text(
        "def register(registry):\n    registry.register_source('demo', object)\n", encoding="utf-8"
    )
    signing.sign_file(plugin_path, private_pem)
    (plugin_dir / "listing.md").write_text("# demo_plug\n测试插件。\n", encoding="utf-8")

    (registry / "authors").mkdir()
    (registry / "authors" / "alice.yaml").write_text(
        f"username: alice\ndisplay_name: alice\npubkey_ref: ../../trust.pub.pem\n"
        f"fingerprint: {fingerprint}\nroles: [publisher]\n",
        encoding="utf-8",
    )
    (plugin_dir / "plugin.yaml").write_text(
        "id: demo_plug\n"
        "name: Demo Plugin\n"
        "version: 1.0.0\n"
        "publisher: alice\n"
        f"author_fingerprint: {fingerprint}\n"
        "category: source\n"
        "summary: test plugin\n"
        "description_file: plugins/demo_plug/listing.md\n"
        "plugin_file: plugins/demo_plug/plugin.py\n"
        "signature_file: plugins/demo_plug/plugin.py.sig\n"
        "signature_algorithm: ed25519\n"
        "permissions: []\n"
        'compatible_core: ">=2.7.0"\n'
        "license: MIT\n",
        encoding="utf-8",
    )
    return registry, trust_pem, private_pem


def _add_market_template(
    registry: Path, trust_pem: Path, private_pem: bytes, *, publisher: str = "alice"
) -> None:
    """在 registry 中构造一个带签名（信任根）的市场模板条目。"""
    template_dir = registry / "templates" / "demo_template"
    template_dir.mkdir(parents=True)
    template_path = template_dir / "template.yaml"
    template_path.write_text(
        "template_version: 1\n"
        "template:\n"
        "  id: demo/template\n"
        "  name: Demo Template\n"
        "  category: generic\n"
        "  description: 测试模板\n"
        "  version: 1.0.0\n"
        "  publisher: alice\n"
        f"  author_fingerprint: {hashlib.sha256(trust_pem.read_bytes()).hexdigest()[:32]}\n"
        "  min_core_version: '2.7.0'\n"
        "  tags: [demo]\n"
        "project: {name: demo, workspace: work/demo}\n"
        "source: {kind: static_html, seeds: ['https://example.com']}\n",
        encoding="utf-8",
    )
    signature = signing.sign_bytes(template_path.read_bytes(), private_pem)
    (template_dir / "template.yaml.sig").write_bytes(signature)
    (template_dir / "listing.md").write_text("# demo_template\n测试模板。\n", encoding="utf-8")


def test_real_registry_passes_check() -> None:
    result = _run("--check", "--registry", str(REAL_REGISTRY))
    assert result.returncode == 0, result.stderr or result.stdout


def test_standalone_copy_passes_check(tmp_path: Path) -> None:
    """拆库演练：整个 registry/ 复制到新位置后自包含可校验（工具 + keys/ 随库走）。"""
    standalone = tmp_path / "registry-standalone"
    shutil.copytree(REAL_REGISTRY, standalone)
    result = _run("--check", "--registry", str(standalone))
    assert result.returncode == 0, result.stderr or result.stdout
    generated = _run("--registry", str(standalone))
    assert generated.returncode == 0, generated.stderr
    assert (standalone / "catalog.json").is_file()


def test_real_registry_list_through_market_tool() -> None:
    """catalog.json 作为生成物仍可被市场 CLI 消费。"""
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "market.py"), "--catalog-url", str(REAL_REGISTRY), "list"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_UTF8_ENV,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "example_news" in result.stdout


def test_generate_writes_catalog_and_check_passes(tmp_path: Path) -> None:
    registry, trust_pem, _ = _build_registry(tmp_path)
    result = _run("--registry", str(registry))
    assert result.returncode == 0, result.stderr

    catalog = json.loads((registry / "catalog.json").read_text(encoding="utf-8"))
    assert catalog["schema_version"] == 1
    assert catalog["publisher"] == "alice"
    assert [entry["id"] for entry in catalog["plugins"]] == ["demo_plug"]
    assert "author_fingerprint" not in catalog["plugins"][0]

    checked = _run("--check", "--registry", str(registry), "--trust", str(trust_pem))
    assert checked.returncode == 0, checked.stderr or checked.stdout


def test_check_detects_drift(tmp_path: Path) -> None:
    registry, trust_pem, _ = _build_registry(tmp_path)
    _run("--registry", str(registry))
    catalog_path = registry / "catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog["plugins"][0]["summary"] = "drifted"
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    result = _run("--check", "--registry", str(registry), "--trust", str(trust_pem))
    assert result.returncode == 1
    assert "不一致" in result.stdout


def test_check_detects_missing_author_fingerprint(tmp_path: Path) -> None:
    registry, trust_pem, _ = _build_registry(tmp_path)
    manifest = registry / "plugins" / "demo_plug" / "plugin.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("author_fingerprint: ", "# author_fingerprint: "),
        encoding="utf-8",
    )
    result = _run("--check", "--registry", str(registry), "--trust", str(trust_pem))
    assert result.returncode == 1
    assert "author_fingerprint" in result.stdout


def test_check_detects_tampered_signature(tmp_path: Path) -> None:
    registry, trust_pem, _ = _build_registry(tmp_path)
    generated = _run("--registry", str(registry))
    assert generated.returncode == 0, generated.stderr
    plugin_path = registry / "plugins" / "demo_plug" / "plugin.py"
    plugin_path.write_bytes(plugin_path.read_bytes() + b"\n# tampered\n")
    result = _run("--check", "--registry", str(registry), "--trust", str(trust_pem))
    assert result.returncode == 1
    assert "签名" in result.stdout


def test_check_detects_unknown_field(tmp_path: Path) -> None:
    registry, _, _ = _build_registry(tmp_path)
    manifest = registry / "plugins" / "demo_plug" / "plugin.yaml"
    manifest.write_text(manifest.read_text(encoding="utf-8") + "sneaky: true\n", encoding="utf-8")
    result = _run("--check", "--registry", str(registry))
    assert result.returncode == 1
    assert "未知字段" in result.stdout


def _add_second_author(registry: Path, *, display_name: str) -> None:
    (registry / "authors" / "alice2.yaml").write_text(
        f"username: alice2\ndisplay_name: {display_name}\n"
        f"pubkey_ref: ../../trust.pub.pem\n"
        f"fingerprint: {'0' * 32}\n"
        f"roles: [publisher]\n",
        encoding="utf-8",
    )


def test_check_detects_duplicate_display_name_without_suffix(tmp_path: Path) -> None:
    registry, _, _ = _build_registry(tmp_path)
    _add_second_author(registry, display_name="alice")
    result = _run("--check", "--registry", str(registry))
    assert result.returncode == 1
    assert "无后缀" in result.stdout


def test_check_accepts_suffixed_duplicate_display_name(tmp_path: Path) -> None:
    registry, trust_pem, _ = _build_registry(tmp_path)
    _add_second_author(registry, display_name="alice-01")
    generated = _run("--registry", str(registry))
    assert generated.returncode == 0, generated.stderr
    result = _run("--check", "--registry", str(registry), "--trust", str(trust_pem))
    assert result.returncode == 0, result.stdout


def test_check_rejects_non_contiguous_suffixes(tmp_path: Path) -> None:
    registry, _, _ = _build_registry(tmp_path)
    _add_second_author(registry, display_name="alice-02")
    result = _run("--check", "--registry", str(registry))
    assert result.returncode == 1
    assert "后缀不连续" in result.stdout


# ── 模板市场 ───────────────────────────────────────────────────


def test_generate_includes_templates(tmp_path: Path) -> None:
    registry, trust_pem, private_pem = _build_registry(tmp_path)
    _add_market_template(registry, trust_pem, private_pem)
    result = _run("--registry", str(registry))
    assert result.returncode == 0, result.stderr

    catalog = json.loads((registry / "catalog.json").read_text(encoding="utf-8"))
    templates = catalog["templates"]
    assert len(templates) == 1
    entry = templates[0]
    assert entry["id"] == "demo/template"
    assert entry["name"] == "Demo Template"
    assert entry["publisher"] == "alice"
    assert entry["template_file"] == "templates/demo_template/template.yaml"
    assert entry["signature_file"] == "templates/demo_template/template.yaml.sig"
    assert entry["compatible_core"] == ">=2.7.0"
    assert entry["description_file"] == "templates/demo_template/listing.md"
    assert "plugin_file" not in entry

    checked = _run("--check", "--registry", str(registry), "--trust", str(trust_pem))
    assert checked.returncode == 0, checked.stdout


def test_check_detects_template_missing_publisher(tmp_path: Path) -> None:
    registry, trust_pem, private_pem = _build_registry(tmp_path)
    _add_market_template(registry, trust_pem, private_pem)
    template_path = registry / "templates" / "demo_template" / "template.yaml"
    template_path.write_text(
        template_path.read_text(encoding="utf-8").replace("  publisher: alice\n", ""),
        encoding="utf-8",
    )
    result = _run("--check", "--registry", str(registry))
    assert result.returncode == 1
    assert "publisher" in result.stdout


def test_check_detects_template_bad_signature(tmp_path: Path) -> None:
    registry, trust_pem, private_pem = _build_registry(tmp_path)
    _add_market_template(registry, trust_pem, private_pem)
    generated = _run("--registry", str(registry))
    assert generated.returncode == 0, generated.stderr
    template_path = registry / "templates" / "demo_template" / "template.yaml"
    template_path.write_bytes(template_path.read_bytes() + b"\n# tampered\n")
    result = _run("--check", "--registry", str(registry), "--trust", str(trust_pem))
    assert result.returncode == 1
    assert "签名" in result.stdout


def test_check_detects_template_drift(tmp_path: Path) -> None:
    registry, trust_pem, private_pem = _build_registry(tmp_path)
    _add_market_template(registry, trust_pem, private_pem)
    _run("--registry", str(registry))
    catalog_path = registry / "catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog["templates"][0]["summary"] = "drifted"
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    result = _run("--check", "--registry", str(registry), "--trust", str(trust_pem))
    assert result.returncode == 1
    assert "不一致" in result.stdout
