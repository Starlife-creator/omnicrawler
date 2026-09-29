"""``plugins catalogs list|add|remove``（#77 Phase 1）的语义用例。

最关键的一条：**add 必须先播种当前生效的源**——否则"第一次显式配置"会把官方源弄丢，
用户升级后市场突然只剩第三方源。
"""

from __future__ import annotations

from pathlib import Path

from omnicrawler.commands import plugins_catalog as cmd

OFFICIAL = "https://raw.githubusercontent.com/Starlife-creator/OmniCrawler-market/main"


def _write_config(tmp_path: Path, *, catalogs: str = "", comment: bool = False) -> Path:
    path = tmp_path / "task.yaml"
    head = "# 用户的重要注释：不要弄丢我\n" if comment else ""
    path.write_text(
        head
        + f"project: {{name: sul, workspace: {(tmp_path / 'work').as_posix()}}}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n"
        "plugins:\n"
        f'  catalog_url: "{OFFICIAL}"\n'
        f"{catalogs}",
        encoding="utf-8",
    )
    return path


def test_list_shows_implicit_official_source(tmp_path: Path) -> None:
    """未配置 catalogs 时也要如实展示"隐式官方源"，否则用户以为一个源都没有。"""
    config = _write_config(tmp_path)

    payload, code = cmd.catalogs_list(config_path=str(config))

    assert code == 0 and payload["status"] == "ok"
    assert payload["explicit"] is False
    assert payload["sources"] == [
        {
            "url": OFFICIAL,
            "kind": "curated",
            "trust": "official",
            "priority": 0,
            "enabled": True,
            "custom_trust_root": False,
            "implicit": True,
        }
    ]


def test_add_seeds_official_source_before_appending(tmp_path: Path) -> None:
    """★ add 必须先把当前生效的官方源写进去，再追加新源（否则官方源被静默丢掉）。"""
    config = _write_config(tmp_path)

    payload, code = cmd.catalogs_add(
        config_path=str(config), url="https://example.com/community-index",
        kind="community", priority=50,
    )

    assert code == 0 and payload["status"] == "ok"
    urls = [s["url"] for s in payload["sources"]]
    assert urls == [OFFICIAL, "https://example.com/community-index"], "官方源仍在，且在前"

    listed, _ = cmd.catalogs_list(config_path=str(config))
    assert listed["explicit"] is True
    assert [s["url"] for s in listed["sources"]] == urls
    kinds = {s["url"]: s["kind"] for s in listed["sources"]}
    assert kinds[OFFICIAL] == "curated"
    assert kinds["https://example.com/community-index"] == "community"


def test_add_same_url_updates_in_place(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    cmd.catalogs_add(config_path=str(config), url="https://example.com/a", priority=90)

    payload, _code = cmd.catalogs_add(
        config_path=str(config), url="https://example.com/a",
        kind="topic", trust="PUBKEY-PEM", priority=5,
    )

    urls = [s["url"] for s in payload["sources"]]
    assert urls.count("https://example.com/a") == 1, "同 URL 不应重复"
    entry = next(s for s in payload["sources"] if s["url"] == "https://example.com/a")
    assert entry["kind"] == "topic" and entry["priority"] == 5 and entry["trust"] == "PUBKEY-PEM"


def test_add_rejects_unknown_kind(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    payload, code = cmd.catalogs_add(
        config_path=str(config), url="https://example.com/a", kind="official-ish"
    )
    assert code == 2 and payload["status"] == "failed"


def test_remove_last_explicit_source_returns_to_implicit(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    cmd.catalogs_add(config_path=str(config), url="https://example.com/a")

    payload, code = cmd.catalogs_remove(config_path=str(config), url="https://example.com/a")

    assert code == 0 and payload["status"] == "ok"
    assert "隐式官方源" in payload["detail"]
    listed, _ = cmd.catalogs_list(config_path=str(config))
    assert listed["explicit"] is False
    assert [s["url"] for s in listed["sources"]] == [OFFICIAL]


def test_remove_unknown_url_fails(tmp_path: Path) -> None:
    config = _write_config(
        tmp_path,
        catalogs='  catalogs:\n    - {url: "https://example.com/a", kind: community}\n',
    )
    payload, code = cmd.catalogs_remove(config_path=str(config), url="https://example.com/nope")
    assert code == 2 and payload["status"] == "failed"


def test_remove_without_explicit_catalogs_fails(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    payload, code = cmd.catalogs_remove(config_path=str(config), url=OFFICIAL)
    assert code == 2 and "没有显式配置" in payload["detail"]


def test_write_back_warns_when_file_had_comments(tmp_path: Path) -> None:
    """★ 写回会重排 YAML 且不保留注释 ⇒ 必须明示，不静默吞掉用户的东西。"""
    config = _write_config(tmp_path, comment=True)

    payload, _code = cmd.catalogs_add(config_path=str(config), url="https://example.com/a")

    assert "不保留注释" in payload["detail"]
    assert "不保留注释" in payload["detail"]
