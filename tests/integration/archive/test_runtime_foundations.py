from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

pytest.importorskip("cryptography")

from omnicrawler.core.config import load_config
from omnicrawler.core.credentials import get_secret, resolve_secret_refs
from omnicrawler.core.logging_utils import JsonFormatter, configure_logging
from omnicrawler.fetching.session import CookieSession, get_cookie_session
from omnicrawler.runtime.schedule_conditions import evaluate_conditions
from omnicrawler.services.doctor import run_doctor


def _config(tmp_path: Path, *, source_kind="static_html", session=None, extra=None):
    value = {
        "project": {"name": "foundation", "workspace": str(tmp_path / "workspace")},
        "source": {"kind": source_kind, "seeds": ["https://example.org"]},
        "http": {"resolve_dns": False, "respect_robots": False},
    }
    if session:
        value["session"] = session
    if extra:
        for key, item in extra.items():
            value.setdefault(key, {}).update(item)
    path = tmp_path / f"{source_kind}.yaml"
    path.write_text(yaml.safe_dump(value), encoding="utf-8")
    return load_config(path)


def test_credentials_environment_keyring_missing_and_recursive_resolution(monkeypatch) -> None:
    monkeypatch.setenv("OMNICRAWL_SECRET_API_KEY", "environment-secret")
    assert get_secret("api-key") == "environment-secret"
    value = {
        "header": "secret://api-key",
        "nested": ["plain", {"token": "secret://api-key"}],
        "number": 3,
    }
    resolved = resolve_secret_refs(value)
    assert resolved["header"] == "environment-secret"
    assert resolved["nested"][1]["token"] == "environment-secret"
    assert resolved["number"] == 3

    monkeypatch.delenv("OMNICRAWL_SECRET_MISSING", raising=False)
    monkeypatch.setitem(sys.modules, "keyring", None)
    with pytest.raises(ValueError, match="OMNICRAWL_SECRET_MISSING"):
        get_secret("missing")


def test_credentials_legacy_prefix_still_works(monkeypatch) -> None:
    """S1.3.8：旧前缀 OMNICRAW_SECRET_* 兼容读取，新前缀优先。"""
    monkeypatch.setenv("OMNICRAW_SECRET_LEGACY", "legacy-value")
    assert get_secret("legacy") == "legacy-value"
    monkeypatch.setenv("OMNICRAWL_SECRET_LEGACY", "new-value")
    assert get_secret("legacy") == "new-value"


def test_credentials_keyring_fallback(monkeypatch) -> None:
    monkeypatch.delenv("OMNICRAW_SECRET_DESKTOP", raising=False)
    fake = SimpleNamespace(get_password=lambda service, name: f"{service}:{name}")
    monkeypatch.setitem(sys.modules, "keyring", fake)
    assert get_secret("desktop") == "omnicrawler:desktop"


def test_cookie_session_memory_persistence_corrupt_load_and_safe_name(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OMNICRAWL_MASTER_PASSWORD", "ci-test-password")
    memory = CookieSession(None)
    memory.save()
    assert memory.path is None

    path = tmp_path / "cookies" / "session.cookies"
    session = CookieSession(path)
    session.save()
    assert path.is_file()
    reloaded = CookieSession(path)
    assert reloaded.path == path

    path.write_text("corrupt", encoding="utf-8")
    assert CookieSession(path).path == path
    with patch("os.chmod", side_effect=OSError("unsupported")):
        CookieSession(path).save()

    transient = get_cookie_session(_config(tmp_path))
    assert transient.path is None
    config = _config(
        tmp_path,
        session={"persist_cookies": True, "name": "account/with unsafe chars"},
    )
    first = get_cookie_session(config)
    second = get_cookie_session(config)
    assert first is second
    assert first.path is not None and first.path.name == "account_with_unsafe_chars.cookies"


def test_schedule_conditions_hours_power_battery_and_network(monkeypatch) -> None:
    from datetime import timezone as _timezone

    assert evaluate_conditions({}) == (True, "")
    fixed = SimpleNamespace(
        now=lambda tz=None: datetime(2026, 1, 1, 9, 0, 0, tzinfo=tz),
        timezone=_timezone,
    )
    monkeypatch.setattr("omnicrawler.runtime.schedule_conditions.datetime", fixed)
    assert evaluate_conditions({"allowed_hours": [10]})[0] is False
    assert evaluate_conditions({"allowed_hours": [9]}) == (True, "")

    psutil = pytest.importorskip("psutil", reason="power and battery checks require optional psutil")

    battery = SimpleNamespace(power_plugged=False, percent=30)
    monkeypatch.setattr(psutil, "sensors_battery", lambda: battery)
    assert evaluate_conditions({"require_ac": True})[1] == "电脑未接通电源"
    assert evaluate_conditions({"minimum_battery_percent": 50})[0] is False
    battery.power_plugged = True
    assert evaluate_conditions({"require_ac": True}) == (True, "")

    down = {"Loopback": SimpleNamespace(isup=True), "Ethernet": SimpleNamespace(isup=False)}
    monkeypatch.setattr(psutil, "net_if_stats", lambda: down)
    assert evaluate_conditions({"require_network": True})[0] is False
    down["Ethernet"].isup = True
    assert evaluate_conditions({"require_network": True}) == (True, "")


def test_json_logging_with_context_and_exception() -> None:
    formatter = JsonFormatter()
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        record = logging.LogRecord(
            "test", logging.ERROR, __file__, 1, "failed %s", ("request",), sys.exc_info()
        )
    record.run_id = "run-1"
    record.url = "https://example.org"
    payload = json.loads(formatter.format(record))
    assert payload["message"] == "failed request"
    assert payload["run_id"] == "run-1"
    assert "RuntimeError: boom" in payload["exception"]

    configure_logging("WARNING", "json")
    root = logging.getLogger()
    assert root.level == logging.WARNING
    assert isinstance(root.handlers[0].formatter, JsonFormatter)
    configure_logging("INFO", "text")
    assert not isinstance(logging.getLogger().handlers[0].formatter, JsonFormatter)


@pytest.mark.parametrize(
    ("kind", "extra", "required", "missing_module"),
    [
        ("browser", {"browser": {"engine": "playwright"}}, "playwright", "playwright"),
        ("websocket", {}, "websockets", "websockets"),
        ("redis", {}, "redis", "redis"),
        ("scrapy", {}, "scrapy", "scrapy"),
        ("static_html", {"http": {"engine": "httpx_async"}}, "httpx_async", "httpx"),
        (
            "static_html",
            {
                "processors": {
                    "pdf": {
                        "enabled": True,
                        "config": str(Path("configs/pdf/generic_template.yaml").resolve()),
                    }
                }
            },
            "pymupdf",
            "fitz",
        ),
    ],
)
def test_doctor_calculates_required_capabilities(
    tmp_path: Path, monkeypatch, kind, extra, required, missing_module
) -> None:
    config = _config(tmp_path, source_kind=kind, extra=extra)
    monkeypatch.setattr(
        "importlib.util.find_spec", lambda name: None if name == missing_module else object()
    )
    monkeypatch.setattr(
        "omnicrawler.doctor.capability_report",
        lambda: {"ok": True, "native": {"chromium": {"ready": False}}},
    )
    monkeypatch.setattr(
        "shutil.disk_usage", lambda _path: SimpleNamespace(total=100, used=20, free=80 * 1024**3)
    )
    report = run_doctor(config)
    assert report["ok"] is False
    assert any(required in error for error in report["errors"])
    assert report["disk_free_gb"] == 80
    assert report["native_runtime"] == {"chromium": {"ready": False}}
