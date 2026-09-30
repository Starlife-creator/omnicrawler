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


def test_feed_url_defaults_to_official_release_page(tmp_path: Path) -> None:
    """未显式配置 feed_url ⇒ 默认＝GitHub 官方发布页的固定链接（用户 2026-09-29 拍板）。

    断言只看 `_resolve` 的产物（纯配置逻辑，**不触网**）；实际取数由其余用例以本地目录覆盖。
    """
    _, public = _keypair()
    config = _write_config(tmp_path, feed=None, public=public)  # 有信任根、无源
    _section, _config, key, base = cmd_self_update._resolve(str(config))
    assert base == uf.DEFAULT_FEED_URL
    assert base.endswith("/releases/latest/download")
    assert key is not None                      # 内置信任根同步接线 ⇒ 默认即"可用"

    # 显式配置仍优先（镜像/本地目录）
    (tmp_path / "mirror").mkdir(parents=True, exist_ok=True)
    explicit = _write_config(tmp_path / "mirror", feed=tmp_path / "mirror-feed", public=public)
    text = explicit.read_text(encoding="utf-8").replace(
        f'  feed_url: "{(tmp_path / "mirror-feed").as_posix()}"',
        '  feed_url: "https://mirror.example.com/omnicrawler"',
    )
    explicit.write_text(text, encoding="utf-8")
    _s, _c, _k, mirror_base = cmd_self_update._resolve(str(explicit))
    assert mirror_base == "https://mirror.example.com/omnicrawler"


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


def _asset_entry(asset: Path | None) -> dict[str, object]:
    """整包条目：给了真实文件就算真哈希（否则下载时会被整包 sha256 校验挡住）。"""
    if asset is None:
        return {"name": "pkg.tar.xz", "sha256": "a" * 64, "size": 999}
    body = asset.read_bytes()
    return {"name": asset.name, "sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}


def _write_payload_feed(
    feed_dir: Path,
    private: ed25519.Ed25519PrivateKey,
    *,
    version: str,
    files: dict[str, bytes],
    deleted: tuple[str, ...] = (),
    delta_path: Path | None = None,
    asset: Path | None = None,
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
        "assets": {"linux-standard": _asset_entry(asset)},
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
    platform_key: str = "linux-standard",
) -> None:
    """只带全量包的清单（大版本形态：没有 payload ⇒ 增量不可用 ⇒ 必须走全量）。"""
    feed_dir.mkdir(parents=True, exist_ok=True)
    body = asset_path.read_bytes()
    document = {
        "version": version,
        "assets": {
            platform_key: {
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
    _write_full_feed(
        feed_dir, private, version="99.0.0", asset_path=pkg, platform_key="windows-standard"
    )
    config = _write_config(tmp_path, feed=feed_dir, public=public)

    payload, code = cmd_self_update.apply(
        config_path=str(config), app_root=app, platform="windows", full=True, to_versions=True
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
    assert payload["options"]["incremental"]["size"] == 42   # 变更包体积（口径上移到 options）
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


def test_apply_incremental_self_heals_when_delta_lacks_needed_members(tmp_path: Path) -> None:
    """★★ 自愈：本机删掉一个"两版之间**没变过**"的文件 ⇒ 增量包必然不含它 ⇒
    **不得硬失败**，改为自动降级走全量，并把缺失路径如实报出来。

    为什么会有这个缺陷：`plan_payload` 把「本机没有的文件」一律算进 `missing_locally`
    （判据是"在不在盘上"，与基线无关），而变更包只装"相对基线**变化过**"的成员。
    于是那个被删掉、但两版间未变的文件**不在包里** ⇒ 原先 `stage_members` 会抛
    「变更包缺少清单里的文件」⇒ 整次更新失败且**不自愈**：用户删过一个文件之后
    再也做不了增量更新（`--full` 能绕开，但报错不指向它）。
    """
    private, public = _keypair()
    feed_dir = tmp_path / "feed"
    app = tmp_path / "app"
    app.mkdir()
    (app / "app.exe").write_bytes(b"OLD")
    # heavy.dll **不在盘上**（用户删过 / 被隔离 / 上次更新中断），但它两版之间没变
    # ⇒ 变更包里没有它 —— 这正是触发条件。
    # 全量包必须真的可取（自愈要落到它上面）。
    full = _write_portable_zip(
        feed_dir / "OmniCrawler-99.0.0-Linux-Portable-Standard.tar.xz",
        files={"app.exe": b"NEW", "heavy.dll": b"SAME-AS-BEFORE"},
    )
    _write_payload_feed(
        feed_dir, private, version="99.0.0",
        files={"app.exe": b"NEW", "heavy.dll": b"SAME-AS-BEFORE"},
        delta_path=_write_delta_package(
            feed_dir / "update-prev-to-new.zip", files={"app.exe": b"NEW"}
        ),
        asset=full,
    )
    config = _write_config(tmp_path, feed=feed_dir, public=public)

    payload, code = cmd_self_update.apply(
        config_path=str(config), app_root=app, platform="linux"
    )

    assert code == cmd_self_update.EXIT_OK, payload
    assert payload["plan"]["mode"] == "full-archive", "缺成员必须降级为全量，而不是硬失败"
    fallback = payload["plan"]["delta_fallback"]
    assert fallback["missing_members"] == ["heavy.dll"]
    assert fallback["missing_count"] == 1
    # 降级后本该装好的东西都要在位
    assert (app / "app.exe").read_bytes() == b"NEW"
    assert (app / "heavy.dll").read_bytes() == b"SAME-AS-BEFORE"
    assert list(app.rglob(f"*{up.ASIDE_MARKER}*")) == []


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


# ── 缺资产：明确报错 + 指引（**不再**指向老版本兜底）─────────────────────


def _write_missing_asset_feed(
    feed_dir: Path, private: ed25519.Ed25519PrivateKey, *, version: str, other_platform: str = "macos"
) -> Path:
    """清单**有** assets，但只有别的平台的键（本平台键缺失）。"""
    feed_dir.mkdir(parents=True, exist_ok=True)
    document = {
        "version": version,
        "assets": {
            f"{other_platform}-standard": {
                "name": "OmniCrawler-99.0.0-macOS-Portable-Standard.dmg",
                "sha256": "a" * 64,
                "size": 123,
            }
        },
    }
    signature = base64.b64encode(private.sign(uf.canonical_feed_bytes(document))).decode()
    path = feed_dir / uf.FEED_FILENAME
    path.write_text(
        json.dumps({**document, "signature": signature}, ensure_ascii=False), encoding="utf-8"
    )
    return path


def test_apply_without_platform_asset_fails_with_guidance(tmp_path: Path) -> None:
    """★ 本平台本版本没有完整包 ⇒ **明确失败 + 给出替代路径**。

    为什么不指向"老版本的全量包"兜底（2026-09-30 用户拍板删除 full_fallback）：
    那会把应用**静默降级成旧版**，却把版本号报成新版（`--to-versions` 还会把它写进
    `current.txt`）—— **一个错误的状态比没有自动路径更糟**。缺本平台键只可能是发布侧出错
    （或该包超 2 GiB 被剔除），如实说出来，并给两条已文档化的替代路径。
    """
    private, public = _keypair()
    feed_dir = tmp_path / "feed"
    app = tmp_path / "app"
    app.mkdir()
    (app / "app.exe").write_bytes(b"OLD")
    _write_missing_asset_feed(feed_dir, private, version="99.0.0")
    config = _write_config(tmp_path, feed=feed_dir, public=public)

    payload, code = cmd_self_update.apply(
        config_path=str(config), app_root=app, platform="linux"
    )

    assert code == cmd_self_update.EXIT_FAILED, payload
    detail = payload["detail"]
    assert "linux-standard" in detail, detail
    # 指引必须真的指路（两条已文档化的替代路径），而不是只说"失败了"
    assert "components import" in detail, detail
    assert "手动下载" in detail, detail
    assert (app / "app.exe").read_bytes() == b"OLD", "失败时一个字节都不该动"


def test_check_without_platform_asset_reports_full_unavailable(tmp_path: Path) -> None:
    """check 侧同样如实：``options.full.available`` 为 False（不再有 via_fallback）。"""
    private, public = _keypair()
    feed_dir = tmp_path / "feed"
    app = tmp_path / "app"
    app.mkdir()
    _write_missing_asset_feed(feed_dir, private, version="99.0.0")
    config = _write_config(tmp_path, feed=feed_dir, public=public)

    payload, code = cmd_self_update.check(
        config_path=str(config), platform="linux", payload_root=app
    )

    assert code == cmd_self_update.EXIT_UPDATE_AVAILABLE, payload
    assert payload["options"]["full"]["available"] is False
    assert "via_fallback" not in payload["options"]["full"]
    assert payload["options"]["install_to_versions"] is False


def test_manifest_without_assets_is_rejected(tmp_path: Path) -> None:
    """★ 反向断言：**没有 assets 的清单必须被拒绝**（统一发布形态后这是发布侧出错）。"""
    private, public = _keypair()
    feed_dir = tmp_path / "feed"
    feed_dir.mkdir(parents=True, exist_ok=True)
    document = {"version": "99.0.0"}
    signature = base64.b64encode(private.sign(uf.canonical_feed_bytes(document))).decode()
    (feed_dir / uf.FEED_FILENAME).write_text(
        json.dumps({**document, "signature": signature}, ensure_ascii=False), encoding="utf-8"
    )
    config = _write_config(tmp_path, feed=feed_dir, public=public)
    app = tmp_path / "app"
    app.mkdir()

    payload, code = cmd_self_update.check(
        config_path=str(config), platform="linux", payload_root=app
    )

    assert code == cmd_self_update.EXIT_FAILED, payload
    assert "assets" in payload["detail"]


def test_cleanup_command_reports_plan_then_deletes(tmp_path: Path) -> None:
    """`self-update cleanup`：缺省只报计划；--yes 才真正删除（与 apply 同一安全判据）。"""
    private, public = _keypair()
    feed_dir = tmp_path / "feed"
    app = tmp_path / "app"
    (app / "_internal").mkdir(parents=True)
    (app / "app.exe").write_bytes(b"OLD")
    _write_payload_feed(
        feed_dir, private, version="99.0.0", files={"app.exe": b"NEW"},
        delta_path=_write_delta_package(feed_dir / "update.zip", files={"app.exe": b"NEW"}),
    )

    # 先装出一个 versions/0.6.4/（模拟布局 B 的历史版本），再把指针切走
    (app / "versions" / "0.4.0").mkdir(parents=True)
    (app / "versions" / "0.4.0" / "app.exe").write_bytes(b"LEGACY")
    # 指针指向 0.6.4（已发布的新版）⇒ versions/0.4.0 成为"不再被指向"的残留
    (app / "versions" / "current.txt").write_text("0.6.4\n", encoding="utf-8")

    plan, code = cmd_self_update.cleanup(app_root=app, yes=False)
    assert code == cmd_self_update.EXIT_OK and plan["status"] == "dry-run"
    # Windows 分隔符：断言用 as_posix 口径
    assert [Path(d["path"]).as_posix() for d in plan["stale_version_dirs"]] == [
        "versions/0.4.0"
    ], "无指针/指针别处 ⇒ 0.4.0 全算残留"
    assert (app / "versions" / "0.4.0").is_dir()      # 计划不删

    done, code = cmd_self_update.cleanup(app_root=app, yes=True)
    assert code == cmd_self_update.EXIT_OK and done["status"] == "cleaned"
    assert done["version_dirs_removed"] == ["0.4.0"]
    assert not (app / "versions" / "0.4.0").exists()
    assert done["freed_bytes"] == len(b"LEGACY")


def test_to_versions_works_on_posix_now_that_consumers_exist(tmp_path: Path) -> None:
    """★★ **撤回旧的「非 Windows 一律拒绝」**：三平台现在都有读指针的启动器了。

    历史：`--to-versions` 曾因"消费者只有 Windows 启动器"而被明确拒绝（静默产出
    "装了但没人会启动它"的布局比拒绝更糟）。现在 Linux 随包发
    `OmniCrawler-launcher` / `omnicrawler-cli-launcher`（读同一份 `versions/current.txt`），
    能力上线 ⇒ 这条拒绝必须同批撤掉，否则用户拿不到已经可用的能力。

    本用例钉三件事：① 在 POSIX 平台**能装成**；② 载荷落在 `<安装根>/versions/<版本>/`
    且指针被写出；③ **启动器原位不动**（它才是入口，退役掉就没法启动了）。
    """
    private, public = _keypair()
    feed_dir = tmp_path / "feed"
    app = tmp_path / "app"
    app.mkdir()
    (app / "app.exe").write_bytes(b"OLD")
    for launcher in ("OmniCrawler-launcher", "omnicrawler-cli-launcher"):
        (app / launcher).write_bytes(b"#!/bin/sh\nexit 0\n")
    pkg = _write_portable_zip(
        feed_dir / "OmniCrawler-99.0.0-Linux-Portable-Standard.tar.xz",
        files={"app.exe": b"NEW", "newfile.txt": b"ADDED"},
    )
    _write_full_feed(feed_dir, private, version="99.0.0", asset_path=pkg)
    config = _write_config(tmp_path, feed=feed_dir, public=public)

    payload, code = cmd_self_update.apply(
        config_path=str(config), app_root=app, platform="linux", full=True, to_versions=True
    )

    assert code == cmd_self_update.EXIT_OK, payload
    assert payload["plan"]["mode"] == "versions-install"
    versions_root = app / "versions" / "99.0.0"
    assert (versions_root / "app.exe").read_bytes() == b"NEW"
    assert (versions_root / "newfile.txt").read_bytes() == b"ADDED"
    assert (app / "versions" / "current.txt").read_text(encoding="utf-8").strip() == "99.0.0"
    # 就地那份**原样不动**＝零成本回退
    assert (app / "app.exe").read_bytes() == b"OLD"
    # ★ 启动器必须还在（退役名单里绝不含启动器）
    for launcher in ("OmniCrawler-launcher", "omnicrawler-cli-launcher"):
        assert (app / launcher).exists(), f"{launcher} 被退役 ⇒ 用户没有入口启动应用"
    # 退役动作**被报告出来了**（具体退役了哪几个由 _retire_names(平台) 决定，
    # 那条不变量另有专门用例钉住；这里只确认这一步执行了、且没碰启动器）。
    assert isinstance(payload["plan"].get("retired_entries"), list)


# ── 按平台清单（update-<platform>.json）+ 平台交叉校验 ─────────────────


def _write_platform_feed(
    feed_dir: Path,
    private: ed25519.Ed25519PrivateKey,
    *,
    version: str,
    platform: str,
    edition: str = "",
) -> Path:
    """写一份**声明了平台**的清单，文件名按平台（可选再加版本）。

    ``edition`` 非空 ⇒ 文件名 ``update-<platform>-<edition>.json``（Standard/Full 载荷不同，
    逐文件清单也不同）；为空 ⇒ ``update-<platform>.json``（粗粒度兼容路径）。
    """
    feed_dir.mkdir(parents=True, exist_ok=True)
    # 注意：`assets` 现在**必须非空**（统一发布形态：每版都发本平台全量包）。
    # 资产键是 `<平台>-<版本>`，与客户端 `asset_key()` 同构；Standard 是缺省版本。
    asset_key = f"{platform}-{edition or 'standard'}"
    pkg_name = f"OmniCrawler-{version}-{platform}-{edition or 'standard'}.tar.xz"
    (feed_dir / pkg_name).write_bytes(b"x")
    document = {
        "version": version,
        "platform": platform,
        "notes": "",
        "assets": {asset_key: {"name": pkg_name, "sha256": "0" * 64, "size": 1}},
    }
    signature = base64.b64encode(private.sign(uf.canonical_feed_bytes(document))).decode()
    path = feed_dir / uf.feed_filename(platform, edition)
    path.write_text(
        json.dumps({**document, "signature": signature}, ensure_ascii=False), encoding="utf-8"
    )
    return path


def test_check_uses_platform_specific_feed_filename(tmp_path: Path) -> None:
    """清单按平台命名 ⇒ 客户端取 ``update-<platform>.json``。"""
    private, public = _keypair()
    feed_dir = tmp_path / "feed"
    app = tmp_path / "app"
    app.mkdir()
    _write_platform_feed(feed_dir, private, version="99.0.0", platform="linux")
    config = _write_config(tmp_path, feed=feed_dir, public=public)

    payload, code = cmd_self_update.check(
        config_path=str(config), platform="linux", payload_root=app
    )

    assert code == cmd_self_update.EXIT_UPDATE_AVAILABLE, payload
    assert payload["latest_version"] == "99.0.0"
    assert payload["feed_document"] == "update-linux.json"


def test_feed_filename_includes_edition_when_given() -> None:
    """★ 文件名契约：带版本 ⇒ ``update-<platform>-<edition>.json``（大小写归一）。"""
    assert uf.feed_filename("Linux", "Standard") == "update-linux-standard.json"
    assert uf.feed_filename("windows", "FULL") == "update-windows-full.json"
    assert uf.feed_filename("macos") == "update-macos.json"


def test_check_prefers_edition_specific_feed_over_platform_one(tmp_path: Path) -> None:
    """★ 反回退断言：本版清单与平台清单**同时存在**时，必须先取本版那份。

    否则 Standard 用户会拿到 Full 的逐文件清单，把 OCR/runtime 的差异文件按错哈希去比。
    """
    private, public = _keypair()
    feed_dir = tmp_path / "feed"
    app = tmp_path / "app"
    app.mkdir()
    # 版本清单报 99.0.0，平台粗粒度清单报 88.0.0 ⇒ 命中哪份一看版本号就知道
    _write_platform_feed(feed_dir, private, version="99.0.0", platform="linux", edition="standard")
    _write_platform_feed(feed_dir, private, version="88.0.0", platform="linux")
    config = _write_config(tmp_path, feed=feed_dir, public=public)

    payload, code = cmd_self_update.check(
        config_path=str(config), platform="linux", edition="Standard", payload_root=app
    )

    assert code == cmd_self_update.EXIT_UPDATE_AVAILABLE, payload
    assert payload["feed_document"] == "update-linux-standard.json"
    assert payload["latest_version"] == "99.0.0", "不得回退到平台粗粒度清单"


def test_check_falls_back_to_platform_feed_when_edition_missing(tmp_path: Path) -> None:
    """兼容路径：更新源只发 ``update-<platform>.json`` ⇒ 仍要能用（逐级回退不是装饰）。"""
    private, public = _keypair()
    feed_dir = tmp_path / "feed"
    app = tmp_path / "app"
    app.mkdir()
    _write_platform_feed(feed_dir, private, version="99.0.0", platform="windows")
    config = _write_config(tmp_path, feed=feed_dir, public=public)

    payload, code = cmd_self_update.check(
        config_path=str(config), platform="windows", edition="Standard", payload_root=app
    )

    assert code == cmd_self_update.EXIT_UPDATE_AVAILABLE, payload
    assert payload["feed_document"] == "update-windows.json"
    assert payload["latest_version"] == "99.0.0"


def test_check_falls_back_to_generic_feed_when_platform_missing(tmp_path: Path) -> None:
    """最粗一层：只有 ``update.json``（声明平台为本机）⇒ 也要能取到。"""
    private, public = _keypair()
    feed_dir = tmp_path / "feed"
    app = tmp_path / "app"
    app.mkdir()
    document = {
        "version": "99.0.0",
        "platform": "linux",
        "assets": {"linux-standard": {"name": "pkg.tar.xz", "sha256": "0" * 64, "size": 1}},
    }
    feed_dir.mkdir(parents=True, exist_ok=True)
    (feed_dir / "pkg.tar.xz").write_bytes(b"x")
    signature = base64.b64encode(private.sign(uf.canonical_feed_bytes(document))).decode()
    (feed_dir / uf.FEED_FILENAME).write_text(
        json.dumps({**document, "signature": signature}, ensure_ascii=False), encoding="utf-8"
    )
    config = _write_config(tmp_path, feed=feed_dir, public=public)

    payload, code = cmd_self_update.check(
        config_path=str(config), platform="linux", edition="Standard", payload_root=app
    )

    assert code == cmd_self_update.EXIT_UPDATE_AVAILABLE, payload
    assert payload["feed_document"] == uf.FEED_FILENAME


def test_apply_reports_which_feed_document_was_used(tmp_path: Path) -> None:
    """``apply --dry-run`` 也要报出命中清单名 —— 出问题时第一句话就能答「用的哪份」。"""
    private, public = _keypair()
    feed_dir = tmp_path / "feed"
    app = tmp_path / "app"
    app.mkdir()
    _write_platform_feed(feed_dir, private, version="99.0.0", platform="linux", edition="standard")
    config = _write_config(tmp_path, feed=feed_dir, public=public)

    payload, code = cmd_self_update.apply(
        config_path=str(config),
        platform="linux",
        edition="Standard",
        dry_run=True,
        app_root=app,
    )

    assert code == cmd_self_update.EXIT_OK, payload
    assert payload["feed_document"] == "update-linux-standard.json"


def test_check_refuses_manifest_from_another_platform(tmp_path: Path) -> None:
    """★ 反向断言：清单声明 linux 而本机 windows ⇒ **拒绝**。

    否则 Windows 客户端会拿 Linux 的逐文件清单去比对/落地，把 ELF/`.so` 覆盖进本机。
    """
    private, public = _keypair()
    feed_dir = tmp_path / "feed"
    app = tmp_path / "app"
    app.mkdir()
    # 同时提供 windows 名字的副本（内容仍是 linux 声明）⇒ 专门测"内容与平台不符"
    _write_platform_feed(feed_dir, private, version="99.0.0", platform="linux")
    (feed_dir / uf.feed_filename("windows")).write_bytes(
        (feed_dir / uf.feed_filename("linux")).read_bytes()
    )
    config = _write_config(tmp_path, feed=feed_dir, public=public)

    payload, code = cmd_self_update.check(
        config_path=str(config), platform="windows", payload_root=app
    )

    assert code == cmd_self_update.EXIT_FAILED, payload
    assert "平台" in payload["detail"] and "linux" in payload["detail"]


# ── 退役入口：绝不碰启动器 ────────────────────────────────────────────────
# 启动器是**版本无关**的入口（读 versions/current.txt 再决定启动哪一份）。
# 把它改名 `.outdated` 等于把"唯一能启动应用的入口"藏起来 —— 此前 Windows 那份
# 就在退役名单里（`OmniCrawler-Launcher.bat`），属真缺陷。


@pytest.mark.parametrize("target_platform", ["windows", "linux", "macos"])
def test_retire_names_never_include_a_launcher(target_platform: str) -> None:
    """★ 不变量：三个平台的退役名单里都不得出现启动器。

    启动器是**版本无关**的入口，退役掉它等于把"唯一能启动应用的入口"藏起来
    （此前 Windows 那份 `OmniCrawler-Launcher.bat` 就在名单里）。
    """
    names = cmd_self_update._retire_names(target_platform)

    assert names, "总要退役点什么（否则就地布局会留下可误点的旧入口）"
    for name in names:
        assert "launcher" not in name.lower(), f"{name} 是启动器，不能退役"
        assert not name.lower().endswith(".bat"), f"{name} 是启动器，不能退役"


def test_retire_names_follow_the_target_platform() -> None:
    """★ 退役名单按**目标平台**给，而不是按"进程跑在哪个系统上"。

    取显式平台才能在测试里确定性地说清行为；生产环境里两者一致
    （动作作用在正在运行的那棵树上）。
    """
    assert cmd_self_update._retire_names("windows") == ("OmniCrawler.exe",)
    assert cmd_self_update._retire_names("linux") == ("OmniCrawler", "omnicrawler")
    assert cmd_self_update._retire_names("macos") == ("OmniCrawler", "omnicrawler")


def test_retire_entries_leave_launchers_untouched(tmp_path: Path) -> None:
    """★ 功能断言：跑一遍退役，启动器必须**原位不动**，具体版本二进制才被改名。

    ★ 用 `platform="windows"`：退役名单里 `OmniCrawler` 与 `omnicrawler` 只差大小写，
    在**大小写不敏感的卷**（macOS 默认、Windows）上会塌成同一个文件 ⇒ 用 POSIX 名单
    时"退了几个"会随文件系统而变。跨平台的那条不变量由上面参数化的用例钉。
    """
    root = tmp_path / "app"
    root.mkdir()
    for name in ("OmniCrawler.exe", "OmniCrawler-Launcher.bat",
                 "OmniCrawler", "OmniCrawler-launcher", "omnicrawler", "omnicrawler-cli-launcher"):
        (root / name).write_bytes(b"stub")

    retired = cmd_self_update._retire_inplace_entries(root, platform="windows")

    for launcher in ("OmniCrawler-Launcher.bat", "OmniCrawler-launcher", "omnicrawler-cli-launcher"):
        assert (root / launcher).exists(), f"{launcher} 被退役了 ⇒ 用户没有入口启动应用"
        assert not (root / f"{launcher}.outdated").exists()

    for name in cmd_self_update._retire_names("windows"):
        assert (root / f"{name}.outdated").exists(), f"{name} 应当被退役"
    assert len(retired) == len(cmd_self_update._retire_names("windows"))


# ── 平台能力声明：macOS 只能手动安装（"能力声明与实现一致"）─────────────────
# macOS 做不到自动落地：`apply_archive()` 只认 zip 而主产物是 dmg、`browsers/` 在 .app
# 之外、ad-hoc 签名会被改坏。此前我们**已经在发** `update-macos-*.json` ⇒ 等于对一个
# 装不上的平台声明"可更新"。现在三处一致：能力表（services）、清单字段、客户端判据。


def _write_manual_only_feed(
    feed_dir: Path, private: ed25519.Ed25519PrivateKey, *, version: str = "99.0.0"
) -> Path:
    """macOS 形态的清单：有 assets（dmg），但声明 `auto_apply: false`。"""
    feed_dir.mkdir(parents=True, exist_ok=True)
    document = {
        "version": version,
        "platform": "macos",
        "edition": "standard",
        "auto_apply": False,
        "assets": {
            "macos-standard": {
                "name": f"OmniCrawler-{version}-macOS-Portable-Standard.dmg",
                "sha256": "c" * 64,
                "size": 900 * 1024 * 1024,
            }
        },
    }
    signature = base64.b64encode(private.sign(uf.canonical_feed_bytes(document))).decode()
    path = feed_dir / uf.feed_filename("macos", "standard")
    path.write_text(
        json.dumps({**document, "signature": signature}, ensure_ascii=False), encoding="utf-8"
    )
    return path


def test_apply_refuses_on_manual_only_platform_with_the_download_hint(tmp_path: Path) -> None:
    """★ 声明"只能手动装"的平台 ⇒ **明确拒绝并给出要下哪个包**，不去尝试注定失败的落地。"""
    private, public = _keypair()
    feed_dir = tmp_path / "feed"
    app = tmp_path / "app"
    app.mkdir()
    (app / "OmniCrawler.app").mkdir()
    _write_manual_only_feed(feed_dir, private)
    config = _write_config(tmp_path, feed=feed_dir, public=public)

    payload, code = cmd_self_update.apply(
        config_path=str(config), app_root=app, platform="macos", edition="Standard"
    )

    assert code == cmd_self_update.EXIT_FAILED, payload
    assert payload["manual_install"] is True
    assert "不支持自动更新" in payload["detail"]
    # 必须**告诉用户下哪一个**（否则"手动装"只是一句空话）
    assert "OmniCrawler-99.0.0-macOS-Portable-Standard.dmg" in payload["detail"]


def test_check_reports_manual_install_for_such_platform(tmp_path: Path) -> None:
    """check 侧同步如实：`options.manual_install` 为真，且体积用真实资产算出来。"""
    private, public = _keypair()
    feed_dir = tmp_path / "feed"
    app = tmp_path / "app"
    app.mkdir()
    _write_manual_only_feed(feed_dir, private)
    config = _write_config(tmp_path, feed=feed_dir, public=public)

    payload, code = cmd_self_update.check(
        config_path=str(config), platform="macos", edition="Standard", payload_root=app
    )

    assert code == cmd_self_update.EXIT_UPDATE_AVAILABLE, payload
    assert payload["options"]["manual_install"] is True
    assert payload["options"]["auto_apply"] is False
    assert payload["options"]["install_to_versions"] is False
    assert payload["options"]["full"]["size"] == 900 * 1024 * 1024
    assert "手动下载" in payload["detail"]


def test_check_keeps_auto_apply_for_supported_platforms(tmp_path: Path) -> None:
    """反向的一半：Windows/Linux 照旧可自动更新（别为了修 macOS 把正常路径关掉）。"""
    private, public = _keypair()
    feed_dir = tmp_path / "feed"
    app = tmp_path / "app"
    app.mkdir()
    _write_platform_feed(feed_dir, private, version="99.0.0", platform="linux")
    config = _write_config(tmp_path, feed=feed_dir, public=public)

    payload, code = cmd_self_update.check(
        config_path=str(config), platform="linux", payload_root=app
    )

    assert code == cmd_self_update.EXIT_UPDATE_AVAILABLE, payload
    assert payload["options"]["manual_install"] is False
    assert payload["options"]["auto_apply"] is True


# ── 受限网络：**明确报错 + 指引**（#88 验收："离线/代理受限网络有明确报错与指引"）──────
# 只兜住异常还不够 ——"读取更新源失败：<原始异常>"对"被墙/没配代理/策略拦了"这件事
# 没有任何可执行信息。指引刻意只加在网络/策略类错误上：验签失败时去折腾网络是误导。


def test_check_network_failure_reports_actionable_guidance(tmp_path: Path, monkeypatch) -> None:
    """网络类失败 ⇒ detail 里必须给出**可执行的**几条路（代理 / 允许域名 / 镜像 / 手动替换）。"""
    from urllib.error import URLError

    private, public = _keypair()
    feed_dir = tmp_path / "feed"
    app = tmp_path / "app"
    app.mkdir()
    config = _write_config(tmp_path, feed=feed_dir, public=public)

    def _boom(*args, **kwargs):
        raise URLError("timed out")

    monkeypatch.setattr(cmd_self_update, "_fetch", _boom)
    payload, code = cmd_self_update.check(config_path=str(config), platform="linux")

    assert code == cmd_self_update.EXIT_FAILED, payload
    detail = payload["detail"]
    assert "可以这样做" in detail, detail
    assert "proxy" in detail and "allowed_domains" in detail and "feed_url" in detail


def test_check_signature_failure_does_not_suggest_network_troubleshooting(tmp_path: Path) -> None:
    """★ 反向的一半：**验签失败**不得被说成"换个网络试试"（那是误导，且会掩盖真问题）。"""
    private, _public = _keypair()
    _other_private, other_public = _keypair()
    feed_dir = tmp_path / "feed"
    app = tmp_path / "app"
    app.mkdir()
    _write_platform_feed(feed_dir, private, version="99.0.0", platform="linux")
    config = _write_config(tmp_path, feed=feed_dir, public=other_public)

    payload, code = cmd_self_update.check(config_path=str(config), platform="linux")

    assert code == cmd_self_update.EXIT_FAILED, payload
    assert "可以这样做" not in payload["detail"], "验签失败不该给网络排查指引"
