"""Remote (URL-based) coverage for the curated plugin-market client.

These tests exercise the ``_is_remote`` branch of ``market_client`` -- the code
path a real end-user hits when installing from a hosted registry over http(s),
rather than from a local sibling checkout.

A local ``ThreadingHTTPServer`` serves a self-signed synthetic market so the
full remote download + ed25519 verify path runs deterministically **without any
external network**. An opt-in live smoke (``@pytest.mark.network``, gated by
``OMNICRAWL_TEST_LIVE_MARKET``) additionally hits the real public market to
confirm the hosted catalog shape.
"""
from __future__ import annotations

import contextlib
import functools
import hashlib
import http.server
import json
import os
import threading
import urllib.error
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from omnicrawler.plugins import market_client, signing


def _build_synthetic_market(root: Path) -> Path:
    """Create a self-signed synthetic market (catalog + signed plugin). Returns the trust-root path."""
    private_pem, public_pem = signing.generate_keypair()
    trust = root / "configs" / "plugin_trust.pub.pem"
    trust.parent.mkdir(parents=True, exist_ok=True)
    trust.write_bytes(public_pem)
    fingerprint = hashlib.sha256(public_pem).hexdigest()[:32]

    plugin_dir = root / "plugins" / "demo"
    plugin_dir.mkdir(parents=True)
    plugin_path = plugin_dir / "plugin.py"
    plugin_path.write_text(
        'PLUGIN_METADATA = {"name": "demo", "version": "1.0.0"}\n'
        "def register(registry):\n"
        '    registry.register_source("demo", lambda *a, **k: None)\n',
        encoding="utf-8",
    )
    signature = signing.sign_bytes(plugin_path.read_bytes(), private_pem)
    (plugin_dir / "plugin.py.sig").write_bytes(signature)
    (plugin_dir / "listing.md").write_text("# demo\n", encoding="utf-8")
    catalog_bytes = json.dumps(
            {
                "schema_version": 1,
                "plugins": [
                    {
                        "id": "demo",
                        "name": "Demo Plugin",
                        "version": "1.0.0",
                        "publisher": "alice",
                        "category": "test",
                        "summary": "remote coverage plugin",
                        "plugin_file": "plugins/demo/plugin.py",
                        "signature_file": "plugins/demo/plugin.py.sig",
                        "description_file": "plugins/demo/listing.md",
                        "signature_algorithm": "ed25519",
                        "author_fingerprint": fingerprint,
                        "compatible_core": ">=2.7.0",
                    }
                ],
                "templates": [],
            }
        ).encode("utf-8")
    (root / "catalog.json").write_bytes(catalog_bytes)
    (root / "catalog.json.sig").write_bytes(signing.sign_bytes(catalog_bytes, private_pem))
    return trust


@pytest.fixture
def remote_market(tmp_path: Path):
    root = tmp_path / "market"
    root.mkdir()
    trust = _build_synthetic_market(root)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}", str(trust)
    finally:
        server.shutdown()
        server.server_close()




class _AllowAllEgress:
    """B01-011: allow-all egress fake for tests.

    契约已升级（市场下载改走与任务抓取同一条安全 opener）：替身必须提供真实的
    ``config`` 与 ``policy``，否则市场客户端无法构建 opener。合成市场监听
    127.0.0.1，因此这里显式打开 ``http.allow_private_network``——它正是给回环/
    内网目标的合法开关，不是放松市场校验。
    """

    def __init__(self, root: Path | None = None) -> None:
        from omnicrawler.core.config import DEFAULTS, AppConfig, deep_merge
        from omnicrawler.security.policy import NetworkTargetPolicy

        base = root or Path("<market-test>")
        raw = deep_merge(dict(DEFAULTS), {"http": {"allow_private_network": True}})
        raw.setdefault("project", {"name": "market-test", "workspace": str(base)})
        self.config = AppConfig(base / "market-test.yaml", base, raw, base)
        self.policy = NetworkTargetPolicy(self.config)

    def request(self, url: str, *, purpose: str = "", headers: dict | None = None):
        return contextlib.nullcontext()

    def authorize(
        self,
        url: str,
        *,
        purpose: str = "",
        headers: dict | None = None,
        capability: object | None = None,
    ) -> None:
        return None


def test_fetch_catalog_remote_routes_to_url(remote_market) -> None:
    url, _ = remote_market
    # remote join must produce an http URL, not a filesystem path
    assert market_client._join(url, "catalog.json").startswith("http://")
    catalog = market_client.fetch_catalog(url, egress=_AllowAllEgress())
    assert catalog["schema_version"] == 1
    assert any(entry["id"] == "demo" for entry in catalog["plugins"])


def test_download_and_verify_remote(remote_market, tmp_path: Path) -> None:
    url, trust = remote_market
    dest = tmp_path / "installed"
    path = market_client.download_and_verify("demo", url, dest, trust, egress=_AllowAllEgress())
    assert path.is_file()
    assert (path.parent / "plugin.py.sig").is_file()
    assert (path.parent / "listing.md").is_file()
    assert (path.parent / market_client._INSTALL_META).is_file()
    ok, reason = market_client.verify_installed(dest, "demo", trust)
    assert ok and reason == "verified"


def test_download_rejects_tampered_remote(remote_market, tmp_path: Path) -> None:
    url, trust = remote_market
    dest = tmp_path / "installed"
    path = market_client.download_and_verify("demo", url, dest, trust, egress=_AllowAllEgress())
    path.write_bytes(path.read_bytes() + b"\n# tampered")
    ok, _ = market_client.verify_installed(dest, "demo", trust)
    assert not ok


def test_legacy_download_cannot_replace_complete_signed_package(
    remote_market, tmp_path: Path
) -> None:
    url, trust = remote_market
    dest = tmp_path / "installed"
    protected = dest / "demo"
    protected.mkdir(parents=True)
    manifest = protected / "package.manifest.json"
    manifest.write_text('{"package_format": 1}', encoding="utf-8")

    with pytest.raises(PermissionError, match="完整签名包"):
        market_client.download_and_verify(
            "demo", url, dest, trust, egress=_AllowAllEgress()
        )

    assert manifest.read_text(encoding="utf-8") == '{"package_format": 1}'
    assert not (protected / "plugin.py").exists()


def test_download_rejects_unknown_remote(remote_market, tmp_path: Path) -> None:
    url, trust = remote_market
    with pytest.raises(KeyError):
        market_client.download_and_verify("nonexistent", url, tmp_path, trust, egress=_AllowAllEgress())


def test_market_read_uses_shared_safe_opener(remote_market, monkeypatch: pytest.MonkeyPatch) -> None:
    """市场下载必须走**与任务抓取同一条**安全 opener（#74 §1）。

    此前市场用裸 ``urlopen``：既不走 ``http.proxy``，也不会逐地址回退，还会把
    「加速器把域名解析到回环」当成内网目标整条拒掉。这里锁死它复用同一条实现。
    """
    from omnicrawler.fetching import http_client

    calls: list[dict] = []
    real = http_client.build_safe_opener

    def spy(config, *, target_policy=None, **kwargs):
        calls.append({"config": config, "target_policy": target_policy, **kwargs})
        return real(config, target_policy=target_policy, **kwargs)

    monkeypatch.setattr(http_client, "build_safe_opener", spy)
    url, _ = remote_market
    egress = _AllowAllEgress()
    catalog = market_client.fetch_catalog(url, egress=egress)

    assert catalog["schema_version"] == 1
    assert len(calls) == 1
    # 复用同一个策略对象 ⇒ 与任务共享 DNS 解析结论与私网判定
    assert calls[0]["target_policy"] is egress.policy
    assert calls[0]["purpose"] == "plugin"
    assert calls[0]["include_cookies"] is False


def test_market_read_honours_configured_proxy(remote_market) -> None:
    """配置了 ``http.proxy`` 就必须**真的**走代理，而不是被静默忽略。

    用一个不可达的代理端口：请求必须失败。若代理被忽略，合成市场会正常返回 200，
    这条就会红 —— 这正是「市场不认代理」的判据。
    """
    url, _ = remote_market
    egress = _AllowAllEgress()
    http_cfg = egress.config.raw.setdefault("http", {})
    assert isinstance(http_cfg, dict)
    http_cfg["proxy"] = "http://127.0.0.1:9"

    with pytest.raises(urllib.error.URLError):
        market_client.fetch_catalog(url, egress=egress)


@pytest.mark.network
@pytest.mark.skipif(
    not os.environ.get("OMNICRAWL_TEST_LIVE_MARKET"),
    reason="真实网络访问公开市场；设 OMNICRAWL_TEST_LIVE_MARKET=1 启用",
)
def test_fetch_catalog_live_remote() -> None:
    """Real-user path: pull the live public market catalog over HTTPS (flaky-safe, opt-in)."""
    url = "https://raw.githubusercontent.com/Starlife-creator/OmniCrawler-market/main"
    last_exc: Exception | None = None
    catalog = None
    for _ in range(3):
        try:
            catalog = market_client.fetch_catalog(url, egress=_AllowAllEgress())
            break
        except Exception as exc:  # noqa: BLE001 - transient network/rate-limit tolerance
            last_exc = exc
    if catalog is None:
        pytest.skip(f"live market 不可达，跳过: {last_exc}")
    assert catalog["schema_version"] == 1
    assert isinstance(catalog.get("plugins"), list)
