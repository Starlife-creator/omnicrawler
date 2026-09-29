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
from omnicrawler.services import updater as up


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
    delta_path: Path | None = None,
) -> None:
    feed_dir.mkdir(parents=True, exist_ok=True)
    # 给了真实变更包就算真实哈希（否则下载时会被整包 sha256 校验挡住）；没给则用占位值
    if delta_path is not None:
        body = delta_path.read_bytes()
        delta_entry = {
            "name": delta_path.name,
            "sha256": hashlib.sha256(body).hexdigest(),
            "size": len(body),
        }
    else:
        delta_entry = {"name": "update-prev-to-new.zip", "sha256": "b" * 64, "size": 42}
    document = {
        "version": version,
        "assets": {"linux-standard": {"name": "pkg.tar.xz", "sha256": "a" * 64, "size": 999}},
        "payload": {
            "files": {
                name: {"sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}
                for name, body in files.items()
            },
            "delta": {__version__: delta_entry},
            "deleted": list(deleted),
        },
    }
    signature = base64.b64encode(private.sign(uf.canonical_feed_bytes(document))).decode()
    (feed_dir / uf.FEED_FILENAME).write_text(
        json.dumps({**document, "signature": signature}, ensure_ascii=False), encoding="utf-8"
    )


def _write_delta_package(path: Path, *, files: dict[str, bytes]) -> Path:
    """变更包：一个**只含变化文件**的 zip（自身不签名，成员逐个对已签名清单核哈希）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return path


def _write_portable_zip(path: Path, *, files: dict[str, bytes]) -> Path:
    """便携包形态：zip 里有一层顶层目录 `OmniCrawler/`（build_windows.ps1 `--root-name`）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for name, body in files.items():
            archive.writestr(f"OmniCrawler/{name}", body)
    return path


def _write_full_feed(
    feed_dir: Path,
    private: ed25519.Ed25519PrivateKey,
    *,
    version: str,
    asset_path: Path,
) -> None:
    """只带全量包的清单（大版本形态：没有 payload ⇒ 增量不可用 ⇒ 必须走全量）。"""
    feed_dir.mkdir(parents=True, exist_ok=True)
    body = asset_path.read_bytes()
    document = {
        "version": version,
        "assets": {
            "linux-standard": {
                "name": asset_path.name,
                "sha256": hashlib.sha256(body).hexdigest(),
                "size": len(body),
            }
        },
    }
    signature = base64.b64encode(private.sign(uf.canonical_feed_bytes(document))).decode()
    (feed_dir / uf.FEED_FILENAME).write_text(
        json.dumps({**document, "signature": signature}, ensure_ascii=False), encoding="utf-8"
    )


def test_apply_full_replaces_in_place_and_keeps_workspace(tmp_path: Path) -> None:
    """全量·就地替换：整包哈希来自**已签名清单**，落地仍是改名让位 ⇒ 只占一份。"""
    private, public = _keypair()
    feed_dir = tmp_path / "feed"
    app = tmp_path / "app"
    (app / "work").mkdir(parents=True)
    (app / "work" / "keep.txt").write_bytes(b"KEEP")
    (app / "app.exe").write_bytes(b"OLD")
    pkg = _write_portable_zip(
        feed_dir / "OmniCrawler-99.0.0-Linux-Portable-Standard.tar.xz",
        files={"app.exe": b"NEW", "newfile.txt": b"ADDED"},
    )
    _write_full_feed(feed_dir, private, version="99.0.0", asset_path=pkg)
    config = _write_config(tmp_path, feed=feed_dir, public=public)

    payload, code = cmd_self_update.apply(
        config_path=str(config), app_root=app, platform="linux", full=True
    )
    assert code == cmd_self_update.EXIT_OK, payload
    assert payload["plan"]["mode"] == "full-archive"
    assert (app / "app.exe").read_bytes() == b"NEW"
    assert (app / "newfile.txt").read_bytes() == b"ADDED"
    assert (app / "work" / "keep.txt").read_bytes() == b"KEEP"     # 受保护路径不动
    assert not (app / ".updates" / "rollback").exists()            # 没有备份副本


def test_apply_to_versions_keeps_in_place_copy_as_fallback(tmp_path: Path) -> None:
    """大版本可选：装到 versions/<新版>/，应用根那份**原样不动**＝零成本回退；
    旧入口改名 `.outdated`（防止误点旧版又触发一次更新）。"""
    private, public = _keypair()
    feed_dir = tmp_path / "feed"
    app = tmp_path / "app"
    app.mkdir()
    (app / "OmniCrawler.exe").write_bytes(b"OLD-LAUNCHER")
    (app / "app.exe").write_bytes(b"OLD")
    pkg = _write_portable_zip(
        feed_dir / "OmniCrawler-99.0.0-Linux-Portable-Standard.tar.xz",
        files={"OmniCrawler.exe": b"NEW-LAUNCHER", "app.exe": b"NEW"},
    )
    _write_full_feed(feed_dir, private, version="99.0.0", asset_path=pkg)
    config = _write_config(tmp_path, feed=feed_dir, public=public)

    payload, code = cmd_self_update.apply(
        config_path=str(config), app_root=app, platform="linux", full=True, to_versions=True
    )
    assert code == cmd_self_update.EXIT_OK, payload
    assert payload["plan"]["mode"] == "versions-install"
    assert (app / "versions" / "99.0.0" / "app.exe").read_bytes() == b"NEW"
    assert (app / "versions" / "current.txt").read_text(encoding="utf-8").strip() == "99.0.0"
    assert (app / "app.exe").read_bytes() == b"OLD"                     # 原样不动
    assert (app / "OmniCrawler.exe").exists() is False
    assert (app / "OmniCrawler.exe.outdated").exists()                  # 旧入口已退役


def test_apply_falls_back_to_full_when_no_delta_for_current(tmp_path: Path) -> None:
    """本机版本不在增量基线内（清单只有全量包）⇒ **自动走全量**，而不是永久卡住。"""
    private, public = _keypair()
    feed_dir = tmp_path / "feed"
    app = tmp_path / "app"
    app.mkdir()
    (app / "app.exe").write_bytes(b"OLD")
    pkg = _write_portable_zip(
        feed_dir / "OmniCrawler-99.0.0-Linux-Portable-Standard.tar.xz", files={"app.exe": b"NEW"}
    )
    _write_full_feed(feed_dir, private, version="99.0.0", asset_path=pkg)
    config = _write_config(tmp_path, feed=feed_dir, public=public)

    payload, code = cmd_self_update.apply(config_path=str(config), app_root=app, platform="linux")
    assert code == cmd_self_update.EXIT_OK, payload
    assert payload["plan"]["mode"] == "full-archive"     # 没有可用增量 ⇒ 全量兜底
    assert (app / "app.exe").read_bytes() == b"NEW"


def test_ignore_version_suppresses_then_recovers(tmp_path: Path) -> None:
    """★ "忽略此版本的更新"：同一版本不再提示；出现更新的版本自动恢复。"""
    private, public = _keypair()
    feed_dir = tmp_path / "feed"
    app = tmp_path / "app"
    app.mkdir()
    _write_payload_feed(
        feed_dir, private, version="99.0.0",
        files={"app.exe": b"NEW"},
        delta_path=_write_delta_package(feed_dir / "update.zip", files={"app.exe": b"NEW"}),
    )
    config = _write_config(tmp_path, feed=feed_dir, public=public)

    ignored, code = cmd_self_update.ignore(config_path=str(config), app_root=app)
    assert code == cmd_self_update.EXIT_OK and ignored["status"] == "ignored"
    assert ignored["ignored_version"] == "99.0.0"

    payload, code = cmd_self_update.check(
        config_path=str(config), platform="linux", payload_root=app
    )
    assert code == cmd_self_update.EXIT_OK
    assert payload["status"] == "ignored"                # 不再提示

    cleared, _ = cmd_self_update.ignore(config_path=str(config), clear=True, app_root=app)
    assert cleared["status"] == "cleared"
    payload, code = cmd_self_update.check(
        config_path=str(config), platform="linux", payload_root=app
    )
    assert payload["status"] == "update-available"       # 恢复提示


def test_check_reports_incremental_and_full_sizes_side_by_side(tmp_path: Path) -> None:
    """★ 体积对比文案（照 B 站弹窗）：两个数并排，用户一眼看到省了多少。"""
    private, public = _keypair()
    feed_dir = tmp_path / "feed"
    app = tmp_path / "app"
    app.mkdir()
    (app / "app.exe").write_bytes(b"OLD")
    _write_payload_feed(
        feed_dir, private, version="99.0.0", files={"app.exe": b"NEW"},
        delta_path=_write_delta_package(feed_dir / "update.zip", files={"app.exe": b"NEW"}),
    )
    config = _write_config(tmp_path, feed=feed_dir, public=public)

    payload, code = cmd_self_update.check(
        config_path=str(config), platform="linux", payload_root=app
    )
    assert code == cmd_self_update.EXIT_UPDATE_AVAILABLE
    options = payload["options"]
    assert options["incremental"]["available"] is True
    # ★ 增量体积＝**变更包文件**的大小（这才是实际要下载的字节数，含 zip 开销），
    #   不是清单里变化文件的净字节（那是 plan.fetch_bytes）。
    assert options["incremental"]["size"] == (feed_dir / "update.zip").stat().st_size
    assert options["full"]["available"] is True
    assert options["full"]["size"] == 999                 # 全量包体积（清单声明）
    assert options["install_to_versions"] is True
    assert "增量约" in payload["detail"] and "全量包" in payload["detail"]


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


# ── 增量落地：只下变化的那一个文件（"不会真的下 2G"的端到端）──────────


def test_apply_incremental_replaces_only_changed_files(tmp_path: Path) -> None:
    """★ 端到端：变更包只含变化文件 ⇒ **只替换那一个**，未变的重依赖连 mtime 都不许动。

    这条同时钉住三件事：① 走的是 incremental（不是整包）；② 未变的文件**没被重写**；
    ③ 清单里的"本版已移除"路径被清掉、改名让位的残留也被清掉。
    """
    private, public = _keypair()
    feed_dir = tmp_path / "feed"
    app = tmp_path / "app"
    (app / "_internal").mkdir(parents=True)
    (app / "app.exe").write_bytes(b"OLD")
    heavy = app / "_internal" / "qt.dll"
    heavy.write_bytes(b"QT-SAME" * 100)
    (app / "stale.pyd").write_bytes(b"GONE-IN-NEW")
    heavy_before = heavy.stat().st_mtime_ns

    _write_payload_feed(
        feed_dir, private, version="99.0.0",
        files={"app.exe": b"NEW", "_internal/qt.dll": b"QT-SAME" * 100},
        deleted=("stale.pyd",),
        delta_path=_write_delta_package(
            feed_dir / "update-prev-to-new.zip", files={"app.exe": b"NEW"}
        ),
    )
    config = _write_config(tmp_path, feed=feed_dir, public=public)

    payload, code = cmd_self_update.apply(
        config_path=str(config), app_root=app, platform="linux"
    )
    assert code == cmd_self_update.EXIT_OK, payload
    assert payload["plan"]["mode"] == "incremental"
    assert payload["plan"]["files_to_replace"] == 1     # 只换一个
    assert payload["applied_files"] == 1
    assert (app / "app.exe").read_bytes() == b"NEW"
    assert heavy.stat().st_mtime_ns == heavy_before, "未变的重依赖被重写了"
    assert not (app / "stale.pyd").exists(), "清单里的「本版已移除」没被清掉"
    assert list(app.rglob(f"*{up.ASIDE_MARKER}*")) == [], "改名让位的残留没清掉"


def test_apply_incremental_rejects_tampered_delta_member(tmp_path: Path) -> None:
    """变更包里的成员内容与**已签名清单**不符 ⇒ 必须整包拒绝（变更包自身不签名）。"""
    private, public = _keypair()
    feed_dir = tmp_path / "feed"
    app = tmp_path / "app"
    app.mkdir()
    (app / "app.exe").write_bytes(b"OLD")

    _write_payload_feed(
        feed_dir, private, version="99.0.0", files={"app.exe": b"NEW"},
        delta_path=_write_delta_package(
            feed_dir / "update-prev-to-new.zip", files={"app.exe": b"TAMPERED"}
        ),
    )
    config = _write_config(tmp_path, feed=feed_dir, public=public)

    payload, code = cmd_self_update.apply(
        config_path=str(config), app_root=app, platform="linux"
    )
    assert code == cmd_self_update.EXIT_FAILED
    assert "哈希与清单不符" in payload["detail"]
    assert (app / "app.exe").read_bytes() == b"OLD"     # 一个字节都没落地
    assert list(app.rglob(f"*{up.ASIDE_MARKER}*")) == []


def test_apply_dry_run_reports_incremental_without_touching_disk(tmp_path: Path) -> None:
    private, public = _keypair()
    feed_dir = tmp_path / "feed"
    app = tmp_path / "app"
    app.mkdir()
    (app / "app.exe").write_bytes(b"OLD")
    _write_payload_feed(
        feed_dir, private, version="99.0.0", files={"app.exe": b"NEW"},
        delta_path=_write_delta_package(
            feed_dir / "update-prev-to-new.zip", files={"app.exe": b"NEW"}
        ),
    )
    config = _write_config(tmp_path, feed=feed_dir, public=public)

    payload, code = cmd_self_update.apply(
        config_path=str(config), app_root=app, platform="linux", dry_run=True
    )
    assert code == cmd_self_update.EXIT_OK
    assert payload["status"] == "dry-run"
    assert payload["mode"] == "incremental"
    assert payload["files_to_replace"] == 1
    assert (app / "app.exe").read_bytes() == b"OLD"      # 未写盘
    assert not (app / ".updates").exists()
