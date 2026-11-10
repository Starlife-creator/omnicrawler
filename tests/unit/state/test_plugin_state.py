from __future__ import annotations

import pytest

from omnicrawler.plugins.plugin_broker import CapabilityBroker, CapabilityError
from omnicrawler.state.state_store import StateStore


def _broker(store: StateStore, *, author: str, schema: int = 1) -> CapabilityBroker:
    return CapabilityBroker(
        permissions={"state:read", "state:write"},
        system_info={"version": "test"},
        state_store=store,
        plugin_id="tideprint-gate",
        plugin_author_fingerprint=author,
        plugin_state_schema=schema,
        project_scope="project-a",
    )


def test_plugin_state_is_scoped_by_author_and_schema(tmp_path) -> None:
    with StateStore(tmp_path / "state.sqlite3") as store:
        first = _broker(store, author="author-a")
        first.dispatch("state.set", {"key": "url.example", "value": {"hash": "abc"}})
        assert first.dispatch("state.get", {"key": "url.example"})["found"] is True
        first.dispatch("state.set", {"key": "nullable", "value": None})
        assert first.dispatch("state.get", {"key": "nullable"}) == {
            "found": True,
            "value": None,
        }

        other_author = _broker(store, author="author-b")
        assert other_author.dispatch("state.get", {"key": "url.example"}) == {
            "found": False,
            "value": None,
        }
        next_schema = _broker(store, author="author-a", schema=2)
        assert next_schema.dispatch("state.get", {"key": "url.example"})["found"] is False
        migrated = next_schema.dispatch(
            "state.migrate", {"source_schema": 1, "strategy": "copy"}
        )
        assert migrated == {"copied": 2, "skipped_sensitive": 0, "schema_version": 2}
        assert next_schema.dispatch("state.get", {"key": "url.example"})["value"] == {
            "hash": "abc"
        }


def test_plugin_state_migration_copies_business_keys_and_skips_sensitive_history(tmp_path) -> None:
    with StateStore(tmp_path / "state.sqlite3") as store:
        namespace = ("project-a", "tideprint-gate", "author-a", 1)
        store.plugin_state_set(namespace, "url.example", {"hash": "abc"})
        store.plugin_state_set(namespace, "login_cookie", "historical-secret")
        store.plugin_state_set(namespace, "session", {"storage": "historical-secret"})
        next_schema = _broker(store, author="author-a", schema=2)
        assert next_schema.dispatch("state.migrate", {"source_schema": 1}) == {
            "copied": 1,
            "skipped_sensitive": 2,
            "schema_version": 2,
        }
        assert next_schema.dispatch("state.get", {"key": "url.example"})["value"] == {"hash": "abc"}
        migrated = store.conn.execute(
            "SELECT state_key FROM plugin_state WHERE schema_version=2"
        ).fetchall()
        assert [row["state_key"] for row in migrated] == ["url.example"]
        # 过滤迁移不冒充历史清理，旧 schema 仍由原版本或宿主流程处理。
        assert store.plugin_state_get(namespace, "login_cookie") == (True, "historical-secret")


@pytest.mark.parametrize("key", ["login_cookie", "session", "auth-token", "credentials"])
def test_plugin_state_rejects_session_material_without_touching_store(tmp_path, key: str) -> None:
    with StateStore(tmp_path / "state.sqlite3") as store:
        broker = _broker(store, author="author-a")
        with pytest.raises(CapabilityError) as error:
            broker.dispatch("state.set", {"key": key, "value": "sensitive"})
        assert error.value.code == "E_PERMISSION"
        # 敏感 state 不是凭据泄漏后的可读取渠道。
        with pytest.raises(CapabilityError) as read_error:
            broker.dispatch("state.get", {"key": key})
        assert read_error.value.code == "E_PERMISSION"


def test_plugin_state_enforces_key_size_and_permission(tmp_path) -> None:
    with StateStore(tmp_path / "state.sqlite3") as store:
        broker = _broker(store, author="author-a")
        with pytest.raises(CapabilityError, match="状态键非法"):
            broker.dispatch("state.set", {"key": "../escape", "value": 1})
        with pytest.raises(CapabilityError, match="64 KiB"):
            broker.dispatch("state.set", {"key": "large", "value": "x" * (65 * 1024)})

        read_only = CapabilityBroker(
            permissions={"state:read"},
            system_info={"version": "test"},
            state_store=store,
            plugin_id="demo",
            project_scope="project-a",
        )
        with pytest.raises(CapabilityError) as error:
            read_only.dispatch("state.set", {"key": "blocked", "value": 1})
        assert error.value.code == "E_PERMISSION"
