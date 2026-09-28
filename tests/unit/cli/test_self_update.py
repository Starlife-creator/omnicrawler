"""``omnicrawler self-update`` 全链路（**本地更新源，全程离线**）。

钉住三件容易"看起来正常"其实失效的事：

1. 缺信任根/更新源 ⇒ **报禁用**（退出码 2），不是静默"已是最新"；
2. 更新源被改动 ⇒ **报验签失败**（退出码 3），不是照常给出可下载资产；
3. ``apply`` **没有显式确认就一个字节都不写**（沿用 core.safe_action 的判据）。
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from omnicrawler import __version__
from omnicrawler.cli._handlers import _registry
from omnicrawler.cli._main import build_parser
from omnicrawler.commands import self_update as cmd_self_update
from omnicrawler.core.safe_action import ConfirmationRequiredError
from omnicrawler.services import update_feed as uf


def _keypair() -> tuple[ed25519.Ed25519PrivateKey, bytes]:
    private = ed25519.Ed25519PrivateKey.generate()
    return private, private.public_key().public_bytes_raw()


def _write_config(tmp_path: Path, *, feed: Path | None, public: bytes | None) -> Path:
    public_line = (
        f'  trusted_public_key: "{base64.b64encode(public).decode()}"\n' if public else ""
    )
    feed_line = f'  feed_url: "{feed.as_posix()}"\n' if feed else ""
    path = tmp_path / "task.yaml"
    path.write_text(
        f"project: {{name: sul, workspace: {(tmp_path / 'work').as_posix()}}}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n"
        "self_update:\n"
        f'  edition: "Standard"\n{feed_line}{public_line}',
        encoding="utf-8",
    )
    return path


def _write_feed(feed_dir: Path, private: ed25519.Ed25519PrivateKey, *, version: str, name: str) -> None:
    feed_dir.mkdir(parents=True, exist_ok=True)
    document = {
        "version": version,
        "published_at": "2026-09-28T00:00:00Z",
        "notes": "测试版要点",
        "assets": {"linux-standard": {"name": name, "sha256": "a" * 64, "size": 3}},
    }
    signature = base64.b64encode(private.sign(uf.canonical_feed_bytes(document))).decode()
    (feed_dir / uf.FEED_FILENAME).write_text(
        json.dumps({**document, "signature": signature}, ensure_ascii=False), encoding="utf-8"
    )


def _write_upgrade_package(
    path: Path,
    private: ed25519.Ed25519PrivateKey,
    *,
    version: str = "0.15.0",
    files: dict[str, bytes] | None = None,
) -> Path:
    payloads = files if files is not None else {"bin/app.txt": b"new-app"}
    manifest = {
        "version": version,
        "files": {name: hashlib.sha256(body).hexdigest() for name, body in payloads.items()},
    }
    signature = base64.b64encode(private.sign(uf.canonical_feed_bytes(manifest))).decode()
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("upgrade.json", json.dumps({**manifest, "signature": signature}))
        for name, body in payloads.items():
            archive.writestr(name, body)
    return path


# ── check ────────────────────────────────────────────────────────────────


def test_check_reports_update_available_from_local_feed(tmp_path: Path) -> None:
    private, public = _keypair()
    feed_dir = tmp_path / "feed"
    _write_feed(feed_dir, private, version="99.0.0", name="pkg.zip")
    config = _write_config(tmp_path, feed=feed_dir, public=public)

    payload, code = cmd_self_update.check(
        config_path=str(config), platform="linux", edition="Standard"
    )
    assert code == cmd_self_update.EXIT_UPDATE_AVAILABLE
    assert payload["status"] == "update-available"
    assert payload["asset"]["name"] == "pkg.zip"
    assert payload["notes"] == "测试版要点"


def test_check_is_up_to_date_when_feed_version_equals_installed(tmp_path: Path) -> None:
    private, public = _keypair()
    feed_dir = tmp_path / "feed"
    _write_feed(feed_dir, private, version=__version__, name="pkg.zip")
    config = _write_config(tmp_path, feed=feed_dir, public=public)

    payload, code = cmd_self_update.check(config_path=str(config), platform="linux")
    assert code == cmd_self_update.EXIT_OK
    assert payload["status"] == "up-to-date"


def test_check_is_disabled_without_trust_root(tmp_path: Path) -> None:
    private, _ = _keypair()
    feed_dir = tmp_path / "feed"
    _write_feed(feed_dir, private, version="99.0.0", name="pkg.zip")
    config = _write_config(tmp_path, feed=feed_dir, public=None)  # 有源、无信任根

    payload, code = cmd_self_update.check(config_path=str(config))
    assert code == cmd_self_update.EXIT_DISABLED
    assert payload["status"] == "disabled"
    assert "trusted_public_key" in payload["detail"]


def test_check_is_disabled_without_feed_url(tmp_path: Path) -> None:
    _, public = _keypair()
    config = _write_config(tmp_path, feed=None, public=public)  # 有信任根、无源

    payload, code = cmd_self_update.check(config_path=str(config))
    assert code == cmd_self_update.EXIT_DISABLED
    assert "feed_url" in payload["detail"]


def test_check_reports_failure_when_feed_is_tampered(tmp_path: Path) -> None:
    private, public = _keypair()
    feed_dir = tmp_path / "feed"
    _write_feed(feed_dir, private, version="99.0.0", name="pkg.zip")
    document = json.loads((feed_dir / uf.FEED_FILENAME).read_text(encoding="utf-8"))
    document["version"] = "0.0.1"  # 改完不重签 ⇒ 必须验签失败
    (feed_dir / uf.FEED_FILENAME).write_text(
        json.dumps(document, ensure_ascii=False), encoding="utf-8"
    )
    config = _write_config(tmp_path, feed=feed_dir, public=public)

    payload, code = cmd_self_update.check(config_path=str(config))
    assert code == cmd_self_update.EXIT_FAILED
    assert "验签失败" in payload["detail"]


# ── apply ────────────────────────────────────────────────────────────────


def test_apply_dry_run_writes_nothing(tmp_path: Path) -> None:
    private, public = _keypair()
    app_root = tmp_path / "app"
    app_root.mkdir()
    config = _write_config(tmp_path, feed=None, public=public)
    package = _write_upgrade_package(tmp_path / "upgrade.zip", private)

    payload, code = cmd_self_update.apply(
        config_path=str(config), package=str(package), dry_run=True, app_root=app_root
    )
    assert code == cmd_self_update.EXIT_OK
    assert payload["status"] == "dry-run"
    assert not (app_root / ".updates").exists()
    assert not (app_root / "bin").exists()


def test_apply_offline_package_replaces_files_and_preserves_workspace(tmp_path: Path) -> None:
    private, public = _keypair()
    app_root = tmp_path / "app"
    (app_root / "work").mkdir(parents=True)
    (app_root / "work" / "user.db").write_bytes(b"user-data")
    (app_root / "bin").mkdir(parents=True)
    (app_root / "bin" / "app.txt").write_bytes(b"old-app")
    config = _write_config(tmp_path, feed=None, public=public)
    package = _write_upgrade_package(tmp_path / "upgrade.zip", private)

    payload, code = cmd_self_update.apply(
        config_path=str(config), package=str(package), app_root=app_root
    )
    assert code == cmd_self_update.EXIT_OK, payload
    assert payload["status"] == "applied"
    assert (app_root / "bin" / "app.txt").read_bytes() == b"new-app"
    assert (app_root / "work" / "user.db").read_bytes() == b"user-data"  # 工作区数据不动


def test_apply_refuses_package_that_touches_workspace_paths(tmp_path: Path) -> None:
    private, public = _keypair()
    app_root = tmp_path / "app"
    (app_root / "work").mkdir(parents=True)
    (app_root / "work" / "user.db").write_bytes(b"user-data")
    config = _write_config(tmp_path, feed=None, public=public)
    package = _write_upgrade_package(
        tmp_path / "upgrade.zip", private, files={"work/evil.txt": b"x"}
    )

    payload, code = cmd_self_update.apply(
        config_path=str(config), package=str(package), app_root=app_root
    )
    assert code == cmd_self_update.EXIT_FAILED
    assert payload["status"] == "failed"
    assert not (app_root / "work" / "evil.txt").exists()
    assert (app_root / "work" / "user.db").read_bytes() == b"user-data"


def test_apply_is_disabled_without_trust_root(tmp_path: Path) -> None:
    _, _unused = _keypair()
    private, _ = _keypair()
    config = _write_config(tmp_path, feed=None, public=None)
    package = _write_upgrade_package(tmp_path / "upgrade.zip", private)

    payload, code = cmd_self_update.apply(
        config_path=str(config), package=str(package), app_root=tmp_path / "app"
    )
    assert code == cmd_self_update.EXIT_DISABLED
    assert payload["status"] == "disabled"


# ── CLI 接线：确认判据与命令注册 ──────────────────────────────────────


def test_handler_requires_explicit_confirmation_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, public = _keypair()
    config = _write_config(tmp_path, feed=None, public=public)
    argv = ["omnicrawler", "self-update", "apply", "-c", str(config)]
    monkeypatch.setattr(sys, "argv", argv)

    args = build_parser().parse_args(argv[1:])
    assert args.self_update_command == "apply"
    with pytest.raises(ConfirmationRequiredError, match="破坏性"):
        _registry["self-update"](args)


def test_self_update_is_registered_and_has_both_subcommands() -> None:
    parser = build_parser()
    assert "self-update" in _registry
    args = parser.parse_args(["self-update", "check", "-c", "x.yaml"])
    assert args.self_update_command == "check"
    apply_args = parser.parse_args(["self-update", "apply", "-c", "x.yaml", "--yes"])
    assert apply_args.confirm is True
    assert apply_args.dry_run is False


# ── 内置信任根默认接线 ──────────────────────────────────────────────────


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def test_bundled_update_trust_root_is_wired_by_default() -> None:
    """未配置 `self_update.trusted_public_key` 时必须**默认读内置信任根**。

    否则"开箱即用"永远落在"已禁用"上 —— 与市场侧 `plugin_trust_public_key`
    当初的同款缺陷（内置公钥不接线）。
    """
    from omnicrawler.core.config import AppConfig

    config = AppConfig(Path("test.yaml"), _repo_root(), {"self_update": {}}, Path("work"), [])
    resolved = config.update_trust_public_key
    # 平台无关比较：Windows 上分隔符是 \，不能直接 endswith("configs/…")
    assert Path(resolved).parts[-2:] == ("configs", "update_trust.pub.pem"), resolved
    assert Path(resolved).is_file()
    # 内置信任根必须能被解成合法 ed25519 公钥（32 字节）
    assert len(uf.decode_public_key(resolved) or b"") == 32

    # 显式配置优先于内置
    explicit = AppConfig(
        Path("test.yaml"), _repo_root(),
        {"self_update": {"trusted_public_key": "hex:" + "ab" * 32}}, Path("work"), [],
    )
    assert explicit.update_trust_public_key == "hex:" + "ab" * 32


def test_update_trust_root_differs_from_market_trust_root() -> None:
    """两把钥匙**刻意不同**：目录根授权沙箱插件，更新根授权替换应用本体。"""
    from omnicrawler.core.config import AppConfig

    config = AppConfig(Path("test.yaml"), _repo_root(), {"self_update": {}}, Path("work"), [])
    assert uf.decode_public_key(config.update_trust_public_key) != uf.decode_public_key(
        config.plugin_trust_public_key
    )


# ── 载荷差异：只下变化文件（"不会真的下 2G"的端到端）──────────────────


def _write_payload_feed(
    feed_dir: Path,
    private: ed25519.Ed25519PrivateKey,
    *,
    version: str,
    files: dict[str, bytes],
    deleted: tuple[str, ...] = (),
) -> None:
    feed_dir.mkdir(parents=True, exist_ok=True)
    document = {
        "version": version,
        "assets": {"linux-standard": {"name": "pkg.tar.xz", "sha256": "a" * 64, "size": 999}},
        "payload": {
            "files": {
                name: {"sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}
                for name, body in files.items()
            },
            "delta": {__version__: {"name": "update-prev-to-new.zip", "sha256": "b" * 64, "size": 42}},
            "deleted": list(deleted),
        },
    }
    signature = base64.b64encode(private.sign(uf.canonical_feed_bytes(document))).decode()
    (feed_dir / uf.FEED_FILENAME).write_text(
        json.dumps({**document, "signature": signature}, ensure_ascii=False), encoding="utf-8"
    )


def test_check_skips_unchanged_files_reports_delta_and_removals(tmp_path: Path) -> None:
    private, public = _keypair()
    feed_dir = tmp_path / "feed"
    local_root = tmp_path / "app"
    (local_root / "_internal").mkdir(parents=True)
    (local_root / "app.exe").write_bytes(b"OLD")
    (local_root / "_internal" / "qt.dll").write_bytes(b"QT-SAME")
    (local_root / "_internal" / "removed.pyd").write_bytes(b"X")

    _write_payload_feed(
        feed_dir, private, version="99.0.0",
        files={"app.exe": b"NEW", "_internal/qt.dll": b"QT-SAME"},
        deleted=("_internal/removed.pyd",),
    )
    config = _write_config(tmp_path, feed=feed_dir, public=public)

    payload, code = cmd_self_update.check(
        config_path=str(config), platform="linux", payload_root=local_root
    )
    assert code == cmd_self_update.EXIT_UPDATE_AVAILABLE
    plan = payload["payload_plan"]
    assert plan["to_fetch"] == ["app.exe"]          # 只下变了的那一个
    assert plan["unchanged"] == 1                   # qt.dll 内容相同 ⇒ 跳过
    assert plan["fetch_bytes"] == 3                 # 只算要下的字节，不是整包 999
    assert plan["delta_for_current_version"] == "update-prev-to-new.zip"
    assert plan["delta_size"] == 42
    assert plan["removed_in_new_version"] == ["_internal/removed.pyd"]
    assert "需更新 1/2 个文件" in payload["detail"]
