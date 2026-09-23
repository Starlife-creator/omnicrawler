"""主仓 market/（内置离线快照）与市场仓 catalog.json 的跨仓一致性守卫。

背景（2026-09-23 审计 + 收口）：
- ``tools/sync_snapshot.py`` 只做镜像、无断言 —— 漂移只能靠断言看住；
- templates 数组守卫自 B4a 起；plugins 数组已按"收口"裁定同步（主仓快照
  从 1 个 example_news 收口为市场仓全量），守卫同步扩展到 plugins；
- "签市场"守卫：市场仓每个模板条目的维护者签名（``template.yaml.sig``）
  必须能用信任根公钥**真实验签**通过（Ed25519），缺文件/坏签名都判红。

判据边界：
- ``generated_at`` / ``sequence`` / ``official_publishers`` 是发布元数据，不比对；
- 市场仓缺席时整组跳过（两仓同级约定，与 test_branding_cross_repo 一致）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MARKET_ROOT = REPO_ROOT.parent / "OmniCrawler-market"

pytestmark = pytest.mark.skipif(
    not (MARKET_ROOT / "catalog.json").is_file(),
    reason="市场仓未 clone（两仓同级约定）",
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _main_catalog() -> dict:
    return _load(REPO_ROOT / "market" / "catalog.json")


def _market_catalog() -> dict:
    return _load(MARKET_ROOT / "catalog.json")


def test_snapshot_templates_match_market_repo() -> None:
    main_templates = _main_catalog().get("templates") or []
    market_templates = _market_catalog().get("templates") or []
    assert [t.get("id") for t in main_templates] == [t.get("id") for t in market_templates], (
        "模板 id 集合漂移：请运行 tools/sync_snapshot.py 重新镜像"
    )
    assert main_templates == market_templates, "模板条目内容漂移（summary/license/versions 等）"


def test_snapshot_plugins_match_market_repo() -> None:
    """C2 收口守卫：plugins 快照已收口为市场全量，此后不得再分叉。"""
    main_plugins = _main_catalog().get("plugins") or []
    market_plugins = _market_catalog().get("plugins") or []
    assert [p.get("id") for p in main_plugins] == [p.get("id") for p in market_plugins], (
        "plugins 快照与市场仓分叉（曾长期 1 vs 7）：请运行 tools/sync_snapshot.py"
    )
    assert main_plugins == market_plugins, "plugins 条目内容漂移"


def _trust_public_key() -> bytes:
    """信任根公钥（PEM 文本）：优先市场仓 keys/，回退主仓快照 keys/。"""
    for candidate in (
        MARKET_ROOT / "keys" / "plugin_trust.pub.pem",
        REPO_ROOT / "market" / "keys" / "plugin_trust.pub.pem",
    ):
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8").strip().encode("utf-8")
    pytest.fail("未找到市场信任根公钥 plugin_trust.pub.pem")


def test_market_template_maintainer_signatures_verify() -> None:
    """C3"签市场"守卫：每个市场模板的维护者签名必须真实验签通过。

    与市场侧 catalog_lib/signing.py 同一语义：Ed25519、签名文件是**原始 64 字节
    detached 签名**（非 base64 文本）、被签数据是 template.yaml 原始字节。
    缺签名文件 / 长度不符 / 验签失败 ⇒ 全部判红（fail-closed）。
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    public_key = serialization.load_pem_public_key(_trust_public_key())
    assert isinstance(public_key, Ed25519PublicKey), "信任根必须是 ed25519 公钥"

    entries = _market_catalog().get("templates") or []
    assert entries, "市场仓 catalog 无模板条目（守卫对象为空即红）"
    problems: list[str] = []
    for entry in entries:
        tid = entry.get("id", "<no-id>")
        template_path = MARKET_ROOT / str(entry.get("template_file", ""))
        sig_path = MARKET_ROOT / str(entry.get("signature_file", ""))
        if not template_path.is_file():
            problems.append(f"{tid}: template.yaml 缺失（{entry.get('template_file')}）")
            continue
        if not sig_path.is_file():
            problems.append(f"{tid}: 维护者签名文件缺失（{entry.get('signature_file')}）")
            continue
        signature = sig_path.read_bytes()
        if len(signature) != 64:
            problems.append(f"{tid}: 签名长度 {len(signature)} != 64（不是原始 Ed25519 签名）")
            continue
        try:
            public_key.verify(signature, template_path.read_bytes())
        except Exception:  # noqa: BLE001 —— 验签失败即不可信
            problems.append(f"{tid}: 维护者签名验签失败（template.yaml 与信任根不匹配）")
    assert not problems, "市场模板维护者签名校验失败：\n" + "\n".join(problems)
