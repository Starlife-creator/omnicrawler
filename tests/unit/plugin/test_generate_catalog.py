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

from cryptography.hazmat.primitives import serialization  # noqa: E402

from omnicrawler.plugins import signing  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
GENERATOR = REPO_ROOT.parent / "OmniCrawler-market" / "tools" / "generate_catalog.py"
REAL_REGISTRY = REPO_ROOT.parent / "OmniCrawler-market"
TRUST_ROOT = REPO_ROOT / "configs" / "plugin_trust.pub.pem"

pytestmark = pytest.mark.skipif(
    not REAL_REGISTRY.is_dir(),
    reason="OmniCrawler-market 仓库未 clone（需与主仓库同级），跳过目录生成测试",
)

_UTF8_ENV = {
    **os.environ,
    "PYTHONIOENCODING": "utf-8",
    "OMNICRAWLER_CACHE_DIR": str((REPO_ROOT / ".tmp" / "catalog-cache").resolve()),
}


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
    """构造最小 registry：一个签名插件 + 一个作者记录。返回 (registry, trust_pem, private_pem)。

    B02-014/B02-024：信任根放在 ``registry/keys/plugin_trust.pub.pem`` 内——
    文件引用包含性校验要求 pubkey_ref 解析后仍在 registry 内；generate 路径验签
    也按 ``keys/plugin_trust.pub.pem`` 查找链自动定位。
    """
    private_pem, public_pem = signing.generate_keypair()
    registry = tmp_path / "registry"
    plugin_dir = registry / "plugins" / "demo_plug"
    plugin_dir.mkdir(parents=True)
    keys_dir = registry / "keys"
    keys_dir.mkdir(parents=True)
    trust_pem = keys_dir / "plugin_trust.pub.pem"
    trust_pem.write_bytes(public_pem)

    # raw32 指纹：SHA-256(ed25519 公钥原始 32 字节) 前 16 字节 hex（与生成器同源）
    _public_key = serialization.load_pem_public_key(public_pem)
    fingerprint = hashlib.sha256(_public_key.public_bytes_raw()).hexdigest()[:32]

    plugin_path = plugin_dir / "plugin.py"
    plugin_path.write_text(
        "def register(registry):\n    registry.register_source('demo', object)\n", encoding="utf-8"
    )
    signing.sign_file(plugin_path, private_pem)
    (plugin_dir / "listing.md").write_text("# demo_plug\n测试插件。\n", encoding="utf-8")

    (registry / "authors").mkdir()
    (registry / "authors" / "alice.yaml").write_text(
        f"username: alice\ndisplay_name: alice\npubkey_ref: ../keys/plugin_trust.pub.pem\n"
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
        'compatible_core: ">=0.7.0"\n'
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
        f"  author_fingerprint: {hashlib.sha256(serialization.load_pem_public_key(trust_pem.read_bytes()).public_bytes_raw()).hexdigest()[:32]}\n"
        "  min_core_version: '0.7.0'\n"
        "  license: Example data terms; free text for templates\n"
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


def test_real_registry_list_through_market_tool_requires_current_signature() -> None:
    """CLI consumes only a catalog whose detached signature matches its bytes."""
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "market.py"), "--catalog-url", str(REAL_REGISTRY), "list"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_UTF8_ENV,
    )
    signature_current = signing.verify_bytes(
        (REAL_REGISTRY / "catalog.json").read_bytes(),
        (REAL_REGISTRY / "catalog.json.sig").read_bytes(),
        str(TRUST_ROOT),
    )
    if signature_current:
        assert result.returncode == 0, result.stderr or result.stdout
        assert "example_news" in result.stdout
    else:
        assert result.returncode != 0
        assert "catalog.json" in (result.stderr or result.stdout)


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


def test_license_explicit_passthrough(tmp_path: Path) -> None:
    """显式声明的 license 原样透传。"""
    registry, trust_pem, _ = _build_registry(tmp_path)
    manifest = registry / "plugins" / "demo_plug" / "plugin.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("license: MIT\n", "license: Apache-2.0\n"),
        encoding="utf-8",
    )
    result = _run("--registry", str(registry))
    assert result.returncode == 0, result.stderr
    catalog = json.loads((registry / "catalog.json").read_text(encoding="utf-8"))
    assert catalog["plugins"][0]["license"] == "Apache-2.0"


def test_license_required_when_omitted(tmp_path: Path) -> None:
    """Phase 1（门 2/A1）：未声明 license → 拒绝（删除隐式 OmniCrawler-MIT 回退）。"""
    registry, trust_pem, _ = _build_registry(tmp_path)
    manifest = registry / "plugins" / "demo_plug" / "plugin.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("license: MIT\n", ""),
        encoding="utf-8",
    )
    result = _run("--registry", str(registry))
    assert result.returncode == 1, result.stdout
    assert "license" in result.stdout


def test_license_rejects_non_allowlisted(tmp_path: Path) -> None:
    """Phase 1（门 2/A2）：白名单外许可（如 GPL-2.0-only）→ 拒绝。"""
    registry, trust_pem, _ = _build_registry(tmp_path)
    manifest = registry / "plugins" / "demo_plug" / "plugin.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("license: MIT\n", "license: GPL-2.0-only\n"),
        encoding="utf-8",
    )
    result = _run("--registry", str(registry))
    assert result.returncode == 1, result.stdout
    assert "白名单" in result.stdout


# ── 门 4：license/execution_mode 变更必须升版（A5）──────────────


def _snapshot_catalog(registry: Path) -> Path:
    """把当前 catalog.json 存为基线快照文件（门 4 --prev-catalog 用）。"""
    snapshot = registry / "prev_catalog.json"
    snapshot.write_text((registry / "catalog.json").read_text(encoding="utf-8"), encoding="utf-8")
    return snapshot


def test_gate4_rejects_license_change_without_bump(tmp_path: Path) -> None:
    """门 4：license 变更但版本未递增 → --check 拒绝。"""
    registry, trust_pem, _ = _build_registry(tmp_path)
    _run("--registry", str(registry))  # 生成 v1.0.0 catalog
    snapshot = _snapshot_catalog(registry)

    # 改 license 但不升版
    manifest = registry / "plugins" / "demo_plug" / "plugin.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("license: MIT\n", "license: Apache-2.0\n"),
        encoding="utf-8",
    )
    _run("--registry", str(registry))  # 重新生成 catalog
    result = _run("--check", "--registry", str(registry), "--trust", str(trust_pem),
                  "--prev-catalog", str(snapshot))
    assert result.returncode == 1, result.stdout
    assert "门 4" in result.stdout


def test_gate4_accepts_license_change_with_bump(tmp_path: Path) -> None:
    """门 4：license 变更且版本递增 → 放行。"""
    registry, trust_pem, _ = _build_registry(tmp_path)
    _run("--registry", str(registry))
    snapshot = _snapshot_catalog(registry)

    manifest = registry / "plugins" / "demo_plug" / "plugin.yaml"
    text = manifest.read_text(encoding="utf-8")
    text = text.replace("license: MIT\n", "license: Apache-2.0\n")
    text = text.replace("version: 1.0.0\n", "version: 1.1.0\n")
    manifest.write_text(text, encoding="utf-8")
    _run("--registry", str(registry))
    result = _run("--check", "--registry", str(registry), "--trust", str(trust_pem),
                  "--prev-catalog", str(snapshot))
    assert result.returncode == 0, result.stdout or result.stderr


def test_gate4_rejects_version_downgrade(tmp_path: Path) -> None:
    """门 4：版本倒退 → 拒绝（即使无字段变更）。"""
    registry, trust_pem, _ = _build_registry(tmp_path)
    _run("--registry", str(registry))  # v1.0.0
    snapshot = _snapshot_catalog(registry)

    manifest = registry / "plugins" / "demo_plug" / "plugin.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("version: 1.0.0\n", "version: 0.9.0\n"),
        encoding="utf-8",
    )
    _run("--registry", str(registry))
    result = _run("--check", "--registry", str(registry), "--trust", str(trust_pem),
                  "--prev-catalog", str(snapshot))
    assert result.returncode == 1, result.stdout
    assert "倒退" in result.stdout


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
    # G1（sha256 固化）：plugin.py 内容篡改先被哈希漂移拦截（比签名校验更早），
    # 报"源不一致"+漂移字段——time-of-check 后门防线的直接信号（检测仍 rc=1）。
    assert "源不一致" in result.stdout
    assert "漂移字段" in result.stdout


def test_check_detects_unknown_field(tmp_path: Path) -> None:
    registry, _, _ = _build_registry(tmp_path)
    manifest = registry / "plugins" / "demo_plug" / "plugin.yaml"
    manifest.write_text(manifest.read_text(encoding="utf-8") + "sneaky: true\n", encoding="utf-8")
    result = _run("--check", "--registry", str(registry))
    assert result.returncode == 1
    assert "未知字段" in result.stdout


def _add_second_author(registry: Path, *, display_name: str) -> None:
    """添加第二个作者：独立密钥放 registry/keys/（避免 B02-012 同公钥多 username 拦截）。"""
    _private_pem, public_pem = signing.generate_keypair()
    _key = serialization.load_pem_public_key(public_pem)
    fp = hashlib.sha256(_key.public_bytes_raw()).hexdigest()[:32]
    (registry / "keys" / f"{fp}.pub.pem").write_bytes(public_pem)
    (registry / "authors" / "alice2.yaml").write_text(
        f"username: alice2\ndisplay_name: {display_name}\n"
        f"pubkey_ref: ../keys/{fp}.pub.pem\n"
        f"fingerprint: {fp}\n"
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
    assert entry["compatible_core"] == ">=0.7.0"
    assert entry["description_file"] == "templates/demo_template/listing.md"
    assert "plugin_file" not in entry

    checked = _run("--check", "--registry", str(registry), "--trust", str(trust_pem))
    assert checked.returncode == 0, checked.stdout


# ── Phase 1 schema 扩展（B1：execution_mode/domains/dependencies）──


def test_execution_mode_default_subprocess(tmp_path: Path) -> None:
    """B1：未声明 execution_mode → catalog 写入缺省 subprocess（无兼容语义）。"""
    registry, trust_pem, _ = _build_registry(tmp_path)
    result = _run("--registry", str(registry))
    assert result.returncode == 0, result.stderr
    catalog = json.loads((registry / "catalog.json").read_text(encoding="utf-8"))
    assert catalog["plugins"][0]["execution_mode"] == "subprocess"


def test_execution_mode_invalid_rejected(tmp_path: Path) -> None:
    """B1：execution_mode 非法枚举 → 拒绝。"""
    registry, trust_pem, _ = _build_registry(tmp_path)
    manifest = registry / "plugins" / "demo_plug" / "plugin.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "license: MIT\n", "license: MIT\nexecution_mode: hybrid\n"
        ),
        encoding="utf-8",
    )
    result = _run("--registry", str(registry))
    assert result.returncode == 1, result.stdout
    assert "execution_mode" in result.stdout


def test_domains_must_be_string_list(tmp_path: Path) -> None:
    """B1：domains 非法类型 → 拒绝。"""
    registry, trust_pem, _ = _build_registry(tmp_path)
    manifest = registry / "plugins" / "demo_plug" / "plugin.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "license: MIT\n", "license: MIT\ndomains: example.com\n"
        ),
        encoding="utf-8",
    )
    result = _run("--registry", str(registry))
    assert result.returncode == 1, result.stdout
    assert "domains" in result.stdout


def test_dependencies_must_be_name_mappings(tmp_path: Path) -> None:
    """B1：dependencies 条目须为含 name 的映射，否则拒绝。"""
    registry, trust_pem, _ = _build_registry(tmp_path)
    manifest = registry / "plugins" / "demo_plug" / "plugin.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "license: MIT\n", "license: MIT\ndependencies:\n  - requests\n"
        ),
        encoding="utf-8",
    )
    result = _run("--registry", str(registry))
    assert result.returncode == 1, result.stdout
    assert "dependencies" in result.stdout


# ── A5 tombstone：下架墓碑条目 ──────────────────────────────────


def test_tombstone_included_in_catalog(tmp_path: Path) -> None:
    """A5：合法 tombstones.json → catalog 增 tombstones 块（保留审计连续性）。"""
    registry, trust_pem, _ = _build_registry(tmp_path)
    (registry / "tombstones.json").write_text(
        json.dumps([{"id": "gone_plugin", "removed_at": "2026-01-01", "reason": "恶意吊销"}]),
        encoding="utf-8",
    )
    result = _run("--registry", str(registry))
    assert result.returncode == 0, result.stderr
    catalog = json.loads((registry / "catalog.json").read_text(encoding="utf-8"))
    assert catalog["tombstones"][0]["id"] == "gone_plugin"


def test_tombstone_conflict_with_existing_rejected(tmp_path: Path) -> None:
    """A5：tombstone 与现存插件目录冲突 → 拒绝（下架条目不得在线）。"""
    registry, trust_pem, _ = _build_registry(tmp_path)
    (registry / "tombstones.json").write_text(
        json.dumps([{"id": "demo_plug", "removed_at": "2026-01-01", "reason": "x"}]),
        encoding="utf-8",
    )
    result = _run("--registry", str(registry))
    assert result.returncode == 1, result.stdout
    assert "冲突" in result.stdout


def test_tombstone_missing_required_field_rejected(tmp_path: Path) -> None:
    """A5：tombstone 缺 removed_at/reason → 拒绝。"""
    registry, trust_pem, _ = _build_registry(tmp_path)
    (registry / "tombstones.json").write_text(
        json.dumps([{"id": "gone_plugin"}]), encoding="utf-8"
    )
    result = _run("--registry", str(registry))
    assert result.returncode == 1, result.stdout
    assert "tombstone" in result.stdout


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
