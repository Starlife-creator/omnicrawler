"""Phase 2b 运行时增强 broker 层契约测试（配额/egress 共现/files 逃逸）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.plugins.plugin_broker import (
    E_EGRESS_BLOCKED,
    CapabilityBroker,
    CapabilityError,
)
from omnicrawler.plugins.plugin_quota import DailyNetworkQuota, QuotaExceededError

pytestmark = pytest.mark.plugin_contract


def _make_broker(**overrides) -> CapabilityBroker:
    kwargs = {"permissions": set(), "system_info": {"version": "t"}}
    kwargs.update(overrides)
    return CapabilityBroker(**kwargs)


class _FakeNetwork:
    def __init__(self, body: bytes = b"<html>ok</html>") -> None:
        self._body = body
        self.calls = 0

    def fetch(self, url: str, method: str = "GET", headers: dict | None = None):
        self.calls += 1
        from omnicrawler.core.models import CrawlRequest, FetchResult

        request = CrawlRequest(url=url, method=method, headers=headers or {})
        return FetchResult(
            request=request, final_url=url, status=200,
            headers={"content-type": "text/html"}, body=self._body,
            elapsed_seconds=0.1,
        )


# ---- D4.4 每日配额 ----


def test_daily_quota_blocks_when_limit_reached(tmp_path: Path) -> None:
    """日级配额超限 → E_QUOTA（请求数维度）。"""
    quota = DailyNetworkQuota(
        {"demo": {"requests": 2}}, path=tmp_path / "quota.json"
    )
    quota.load()
    network = _FakeNetwork()
    broker = _make_broker(
        permissions={"network:scoped"},
        network_client=network,
        daily_quota=quota,
        plugin_id="demo",
    )
    broker.dispatch("network.fetch", {"url": "https://example.com/1"})
    broker.dispatch("network.fetch", {"url": "https://example.com/2"})
    with pytest.raises(CapabilityError) as exc_info:
        broker.dispatch("network.fetch", {"url": "https://example.com/3"})
    assert exc_info.value.code == "E_QUOTA"


def test_daily_quota_unlimited_without_rules(tmp_path: Path) -> None:
    quota = DailyNetworkQuota({}, path=tmp_path / "quota.json")
    network = _FakeNetwork()
    broker = _make_broker(
        permissions={"network:scoped"}, network_client=network, daily_quota=quota
    )
    for _ in range(5):
        broker.dispatch("network.fetch", {"url": "https://example.com/x"})
    assert network.calls == 5


def test_daily_quota_persists(tmp_path: Path) -> None:
    """配额状态跨会话持久化（JSON 文件）。"""
    quota_file = tmp_path / "quota.json"
    quota = DailyNetworkQuota({"demo": {"requests": 3}}, path=quota_file)
    quota.load()
    quota.account("demo", requests=3)
    quota.persist()

    reloaded = DailyNetworkQuota({"demo": {"requests": 3}}, path=quota_file)
    reloaded.load()
    with pytest.raises(QuotaExceededError):
        reloaded.check("demo")


def test_quota_exceeded_error_semantics() -> None:
    err = QuotaExceededError("插件 x 今日网络请求已达配额 5")
    assert "配额" in str(err)
    assert isinstance(err, Exception)


# ---- J2 data_egress_policy 共现检测 ----


def test_egress_policy_block_rejects_read_then_fetch() -> None:
    """records.read 后 fetch → E_EGRESS_BLOCKED（block 档阻断外传通道）。"""
    network = _FakeNetwork()
    broker = _make_broker(
        permissions={"records:read", "network:scoped"},
        network_client=network,
        egress_policy="block",
        state_store=_FakeStateStore(),
        run_id="run-x",
    )
    broker.dispatch("records.read", {"limit": 5})
    with pytest.raises(CapabilityError) as exc_info:
        broker.dispatch("network.fetch", {"url": "https://example.com/exfil"})
    assert exc_info.value.code == E_EGRESS_BLOCKED
    assert network.calls == 0  # 未发出请求


class _FakeStateStore:
    def rows(self, sql, params):
        return []
    def _require_run_id(self, run_id):
        pass


def test_egress_policy_prompt_allows_with_audit() -> None:
    """默认 prompt：共现允许但审计留痕（decision=cooccurrence_risk）。"""
    events: list[tuple[str, dict]] = []
    network = _FakeNetwork()
    broker = _make_broker(
        permissions={"records:read", "network:scoped"},
        network_client=network,
        egress_policy="prompt",
        audit_hook=lambda action, details: events.append((action, details)),
        state_store=_FakeStateStore(),
        run_id="run-x",
        plugin_id="demo",
    )
    broker.dispatch("records.read", {"limit": 1})
    result = broker.dispatch("network.fetch", {"url": "https://example.com/ok"})
    assert result["status"] == 200
    cooccurrences = [e for e in events if e[0] == "plugin.egress_cooccurrence"]
    assert cooccurrences and cooccurrences[0][1]["decision"] == "cooccurrence_risk"
    assert cooccurrences[0][1]["records_read_before"] == 1


# ---- C3 files:read 逃逸拒绝 ----


def test_files_read_escape_via_path_traversal_rejected(tmp_path: Path) -> None:
    """路径穿越逃逸：命中白名单前缀但 `..` 解析出库 → E_PERMISSION。"""
    allow_dir = tmp_path / "allow"
    allow_dir.mkdir()
    inside = allow_dir / "data.txt"
    inside.write_text("ok", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")

    broker = _make_broker(
        permissions={"files:read"}, input_files=(str(allow_dir),)
    )
    # 白名单目录内真实文件 OK
    result = broker.dispatch("files.read", {"path": str(inside)})
    assert result["content_b64"]
    # `..` 穿越：前缀命中 allow/ 但解析后指向库外 → 逃逸拒绝
    traversal = str(allow_dir / ".." / "secret.txt")
    with pytest.raises(CapabilityError) as exc_info:
        broker.dispatch("files.read", {"path": traversal})
    assert exc_info.value.code == "E_PERMISSION"
    assert "逃逸" in str(exc_info.value)


def test_files_read_escape_via_symlink_rejected(tmp_path: Path) -> None:
    """符号链接逃逸（平台支持时）；Windows 无特权静默失败则跳过。"""
    allow_dir = tmp_path / "allow"
    allow_dir.mkdir()
    inside = allow_dir / "data.txt"
    inside.write_text("ok", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    link = allow_dir / "link.txt"
    try:
        link.symlink_to(secret)
    except OSError:
        pytest.skip("当前环境无符号链接创建权限")
    if not link.is_symlink():
        pytest.skip("符号链接静默创建失败（Windows 无开发者模式）")

    broker = _make_broker(
        permissions={"files:read"}, input_files=(str(allow_dir),)
    )
    with pytest.raises(CapabilityError) as exc_info:
        broker.dispatch("files.read", {"path": str(link)})
    assert exc_info.value.code == "E_PERMISSION"


def test_files_read_requires_allowlist_membership(tmp_path: Path) -> None:
    allow = tmp_path / "allow.txt"
    allow.write_text("ok", encoding="utf-8")
    broker = _make_broker(permissions={"files:read"}, input_files=(str(allow),))
    with pytest.raises(CapabilityError) as exc_info:
        broker.dispatch("files.read", {"path": str(tmp_path / "other.txt")})
    assert exc_info.value.code == "E_PERMISSION"
