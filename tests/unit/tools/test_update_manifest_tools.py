"""CI 产无签名清单 + 本机只签名（#A.12 改进）的端到端与反向断言。

为什么要这条流水线：逐文件哈希需要一个**已解压载荷目录**才算得出，过去只能在维护者本机做
⇒ 每版发布都要搬 515MB。拆开之后 CI 只算哈希（无密钥），本机只签名 ⇒ 每次发布一条命令。
本文件钉住三件事：① 拆分后**端到端仍然验签通过**；② **无签名清单不可发布**；
③ 误把已签名清单再签一次会被拒绝。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parents[3]
BUILD_TOOL = REPO_ROOT / "tools" / "build_update_manifest.py"
SIGN_TOOL = REPO_ROOT / "tools" / "sign_update_manifest.py"

sys.path.insert(0, str(REPO_ROOT / "src"))

from omnicrawler.plugins import signing  # noqa: E402
from omnicrawler.services.update_feed import decode_public_key, verify_feed_document  # noqa: E402


def _run(tool: Path, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(tool), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=cwd or REPO_ROOT,
    )


def _load_module(name: str, path: Path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _payload(tmp_path: Path) -> Path:
    payload = tmp_path / "payload"
    payload.mkdir(parents=True, exist_ok=True)
    (payload / "app.exe").write_bytes(b"hello")
    (payload / "lib.dll").write_bytes(b"world")
    return payload


def _payload_zip(
    tmp_path: Path, *, root: str = "OmniCrawler", extra: Mapping[str, bytes] | None = None
) -> Path:
    """造一个「便携包形态」的 zip：单顶层目录 ``<root>/``。"""
    import zipfile

    tmp_path.mkdir(parents=True, exist_ok=True)
    archive = tmp_path / f"{root}-9.9.9.zip"
    payload = _payload(tmp_path)
    with zipfile.ZipFile(archive, "w") as handle:
        for path in sorted(payload.rglob("*")):
            handle.write(path, f"{root}/{path.relative_to(payload).as_posix()}")
        for relative, body in (extra or {}).items():
            handle.writestr(f"{root}/{relative}", body)
    return archive


def _payload_tar_xz(tmp_path: Path, *, root: str = "OmniCrawler") -> Path:
    """造一个 tar.xz 形态的便携包（顶层 ``<root>/``）。"""
    import tarfile

    tmp_path.mkdir(parents=True, exist_ok=True)
    archive = tmp_path / f"{root}-9.9.9.tar.xz"
    payload = _payload(tmp_path)
    with tarfile.open(archive, "w:xz") as handle:
        for path in sorted(payload.rglob("*")):
            handle.add(path, arcname=f"{root}/{path.relative_to(payload).as_posix()}")
    return archive


def _asset(tmp_path: Path) -> str:
    """一张 dummy 整包资产：客户端要求清单里**至少有一处可下载**（assets 或 full_fallback）。"""
    archive = tmp_path / "OmniCrawler-9.9.9-Linux-Portable-Standard.tar.xz"
    archive.write_bytes(b"fake-package")
    return f"linux-standard={archive}"


def test_emit_unsigned_then_sign_verifies_end_to_end(tmp_path: Path) -> None:
    """① 拆分后仍端到端可用：CI 产无签名清单 → 本机签名 → 客户端验签通过。"""
    payload = _payload(tmp_path)
    unsigned = tmp_path / "update.unsigned.json"
    built = _run(BUILD_TOOL, "--payload-dir", str(payload), "--version", "9.9.9",
                 "--asset", _asset(tmp_path), "--emit-unsigned", str(unsigned))
    assert built.returncode == 0, built.stdout + built.stderr
    document = json.loads(unsigned.read_text(encoding="utf-8"))
    assert "signature" not in document, "无签名清单里不该有 signature"
    assert set(document["payload"]["files"]) == {"app.exe", "lib.dll"}

    private, public = signing.generate_keypair()
    key_path = tmp_path / "k.pem"
    key_path.write_bytes(private)
    signed_path = tmp_path / "update.json"
    signed = _run(SIGN_TOOL, "--unsigned", str(unsigned), "--key", str(key_path),
                  "--out", str(signed_path))
    assert signed.returncode == 0, signed.stdout + signed.stderr

    feed = verify_feed_document(
        signed_path.read_bytes(), trusted_public_key=decode_public_key(public.decode())
    )
    assert feed.version == "9.9.9"
    assert len(feed.payload_files) == 2


def test_unsigned_manifest_is_not_publishable(tmp_path: Path) -> None:
    """② ★ 反向断言：**无签名清单必须被客户端拒绝**（否则"拆分"会变成"漏签也能发"）。"""
    payload = _payload(tmp_path)
    unsigned = tmp_path / "update.unsigned.json"
    _run(BUILD_TOOL, "--payload-dir", str(payload), "--version", "9.9.9",
         "--emit-unsigned", str(unsigned))
    _private, public = signing.generate_keypair()

    with pytest.raises(Exception) as excinfo:
        verify_feed_document(
            unsigned.read_bytes(), trusted_public_key=decode_public_key(public.decode())
        )
    assert "signature" in str(excinfo.value)


def test_signer_refuses_already_signed_input(tmp_path: Path) -> None:
    """③ 误把已签名清单再签一次 ⇒ 明确拒绝（规范化字节要算在不含 signature 的文档上）。"""
    payload = _payload(tmp_path)
    unsigned = tmp_path / "update.unsigned.json"
    _run(BUILD_TOOL, "--payload-dir", str(payload), "--version", "9.9.9",
         "--emit-unsigned", str(unsigned))
    private, _public = signing.generate_keypair()
    key_path = tmp_path / "k.pem"
    key_path.write_bytes(private)
    signed_path = tmp_path / "update.json"
    assert _run(SIGN_TOOL, "--unsigned", str(unsigned), "--key", str(key_path),
                "--out", str(signed_path)).returncode == 0

    again = _run(SIGN_TOOL, "--unsigned", str(signed_path), "--key", str(key_path),
                 "--out", str(tmp_path / "twice.json"))
    assert again.returncode != 0
    assert "已签名" in (again.stdout + again.stderr)


def test_build_requires_exactly_one_of_key_or_emit_unsigned(tmp_path: Path) -> None:
    """④ fail-closed：`--key` 与 `--emit-unsigned` 必须二选一（都不给或都给都报错）。"""
    payload = _payload(tmp_path)
    neither = _run(BUILD_TOOL, "--payload-dir", str(payload), "--version", "9.9.9")
    assert neither.returncode != 0

    private, _public = signing.generate_keypair()
    key_path = tmp_path / "k.pem"
    key_path.write_bytes(private)
    both = _run(BUILD_TOOL, "--payload-dir", str(payload), "--version", "9.9.9",
                "--emit-unsigned", str(tmp_path / "u.json"), "--key", str(key_path))
    assert both.returncode != 0
    assert "二选一" in (both.stdout + both.stderr)


def test_load_unsigned_from_url_is_supported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """⑤ 一条命令的形态：清单来源可以是 URL。

    ★ 用 monkeypatch 替掉 ``urlopen`` 而不是真发请求，也不用 ``file://``
    （Windows 上 urllib 对 file:// 支持不一致，会让用例变成"平台相关"）。这里要钉的是
    **取件路径本身**：URL 分支能读出文档、并且照样拒绝"已签名"的输入。
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("sign_update_manifest", SIGN_TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    payload = _payload(tmp_path)
    unsigned = tmp_path / "update.unsigned.json"
    _run(BUILD_TOOL, "--payload-dir", str(payload), "--version", "9.9.9",
         "--asset", _asset(tmp_path), "--emit-unsigned", str(unsigned))
    body = unsigned.read_bytes()

    class _Response:
        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return body

    monkeypatch.setattr(module.urllib.request, "urlopen", lambda url, timeout=0: _Response())
    document, source = module.load_unsigned(path=None, url="https://example.com/u.json")
    assert document["version"] == "9.9.9" and source == "https://example.com/u.json"

    # 已签名输入走 URL 也必须被拒（同一条 fail-closed 语义）
    signed_body = json.dumps({**document, "signature": "x"}).encode()
    monkeypatch.setattr(
        module.urllib.request, "urlopen",
        lambda url, timeout=0: type("R", (_Response,), {"read": lambda self: signed_body})(),
    )
    with pytest.raises(ValueError) as excinfo:
        module.load_unsigned(path=None, url="https://example.com/update.json")
    assert "已签名" in str(excinfo.value)


def test_build_platform_flag_writes_field_and_validates_asset(tmp_path: Path) -> None:
    """`--platform` 写进清单、并校验资产键前缀（三平台各一份清单的前提）。"""
    payload = _payload(tmp_path)
    out = tmp_path / "u.json"
    ok = _run(BUILD_TOOL, "--payload-dir", str(payload), "--version", "9.9.9",
              "--platform", "windows", "--asset", str(_asset(tmp_path)).replace(
                  "linux-standard", "windows-standard"),
              "--emit-unsigned", str(out))
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert json.loads(out.read_text(encoding="utf-8"))["platform"] == "windows"

    # 反向：资产键与平台前缀不符 ⇒ 拒绝（防止把 A 平台的包挂到 B 平台的清单上）
    bad = _run(BUILD_TOOL, "--payload-dir", str(payload), "--version", "9.9.9",
               "--platform", "macos", "--asset", _asset(tmp_path),
               "--emit-unsigned", str(tmp_path / "bad.json"))
    assert bad.returncode != 0
    assert "不匹配" in (bad.stdout + bad.stderr)


# ── 构建期从**归档本体**算清单（--payload-archive）─────────────────────────
# 为什么要这条路：清单必须与用户下载到的归档**逐字节一致**。从暂存目录算就多出一条
# "打包时排除了什么"的隐含约定（tar 排除了 OmniCrawler/logs、zip 排除了顶层 logs/…），
# 两处一旦不同步，增量校验会**很晚**才失败（用户侧）。构建脚本正是用这条路。


@pytest.mark.parametrize("builder", [_payload_zip, _payload_tar_xz])
def test_payload_archive_matches_payload_dir_result(tmp_path: Path, builder) -> None:
    """归档与目录两条路算出的逐文件清单**必须一致**（含剥根、哈希、体积）。"""
    payload = _payload(tmp_path)
    from_dir = tmp_path / "from-dir.json"
    ok = _run(BUILD_TOOL, "--payload-dir", str(payload), "--version", "9.9.9",
              "--emit-unsigned", str(from_dir))
    assert ok.returncode == 0, ok.stdout + ok.stderr

    archive = builder(tmp_path / "arc")
    from_arc = tmp_path / "from-arc.json"
    ok2 = _run(BUILD_TOOL, "--payload-archive", str(archive), "--version", "9.9.9",
               "--emit-unsigned", str(from_arc))
    assert ok2.returncode == 0, ok2.stdout + ok2.stderr

    left = json.loads(from_dir.read_text(encoding="utf-8"))["payload"]["files"]
    right = json.loads(from_arc.read_text(encoding="utf-8"))["payload"]["files"]
    assert right == left, "归档路径必须剥掉顶层目录，并算出同样的哈希与体积"
    assert set(right) == {"app.exe", "lib.dll"}


def test_payload_archive_rejects_protected_top_level(tmp_path: Path) -> None:
    """★ 反向断言：归档里出现 ``work/`` ⇒ 拒绝（不能静默漏掉用户数据目录）。"""
    archive = _payload_zip(tmp_path, extra={"work/secret.txt": b"user-data"})
    result = _run(BUILD_TOOL, "--payload-archive", str(archive), "--version", "9.9.9",
                  "--emit-unsigned", str(tmp_path / "u.json"))
    assert result.returncode != 0
    assert "受保护顶层路径 work/" in (result.stdout + result.stderr)
    assert not (tmp_path / "u.json").exists(), "被拒绝时不得留下半成品清单"


def test_payload_archive_rejects_multiple_top_level_roots(tmp_path: Path) -> None:
    """★ 反向断言：顶层目录不唯一 ⇒ 拒绝（否则"剥根"是猜的，清单路径会错位）。"""
    import zipfile

    archive = tmp_path / "two-roots.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("OmniCrawler/app.exe", b"a")
        handle.writestr("other/lib.dll", b"b")
    result = _run(BUILD_TOOL, "--payload-archive", str(archive), "--version", "9.9.9",
                  "--emit-unsigned", str(tmp_path / "u.json"))
    assert result.returncode != 0
    assert "顶层目录不唯一" in (result.stdout + result.stderr)


def test_payload_dir_and_archive_are_mutually_exclusive(tmp_path: Path) -> None:
    """两条载荷来源同时给出 ⇒ 报错（而不是悄悄挑一条，让"清单对的是哪个"变成猜测）。"""
    archive = _payload_zip(tmp_path)
    result = _run(BUILD_TOOL, "--payload-dir", str(_payload(tmp_path)),
                  "--payload-archive", str(archive),
                  "--version", "9.9.9", "--emit-unsigned", str(tmp_path / "u.json"))
    assert result.returncode != 0
    assert "二选一" in (result.stdout + result.stderr)


# ── (平台, 版本) 两维清单 ─────────────────────────────────────────────────


def test_edition_field_and_asset_key_validation(tmp_path: Path) -> None:
    """``--edition`` 写进清单，且要求资产键是 ``<平台>-<版本>``。"""
    payload = _payload(tmp_path)
    out = tmp_path / "u.json"
    ok = _run(BUILD_TOOL, "--payload-dir", str(payload), "--version", "9.9.9",
              "--platform", "linux", "--edition", "standard",
              "--asset", _asset(tmp_path), "--emit-unsigned", str(out))
    assert ok.returncode == 0, ok.stdout + ok.stderr
    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["edition"] == "standard" and document["platform"] == "linux"

    # 反向 A：声明了 --edition 却挂 full 的包 ⇒ 拒绝
    full_pkg = tmp_path / "OmniCrawler-9.9.9-Linux-Portable-Full.tar.xz"
    full_pkg.write_bytes(b"full")
    mismatched = _run(BUILD_TOOL, "--payload-dir", str(payload), "--version", "9.9.9",
                      "--platform", "linux", "--edition", "standard",
                      "--asset", f"linux-full={full_pkg}",
                      "--emit-unsigned", str(tmp_path / "bad1.json"))
    assert mismatched.returncode != 0
    assert "必须是" in (mismatched.stdout + mismatched.stderr)

    # 反向 B：只给 --edition 不给 --platform ⇒ 拒绝（文件名要平台+版本两段）
    orphan = _run(BUILD_TOOL, "--payload-dir", str(payload), "--version", "9.9.9",
                  "--edition", "standard", "--emit-unsigned", str(tmp_path / "bad2.json"))
    assert orphan.returncode != 0
    assert "--platform" in (orphan.stdout + orphan.stderr)


def test_default_output_name_is_platform_then_edition(tmp_path: Path) -> None:
    """默认文件名就是客户端会去找的那个：``update-<平台>-<版本>.json``。

    这里必须真的走一遍：客户端按文件名取清单，文件名错了整条更新链路就静默断在
    "取不到清单"上（而不是报一个能读懂的错误）。
    """
    from omnicrawler.services.update_feed import feed_filename

    payload = _payload(tmp_path)
    private, _public = signing.generate_keypair()
    key_path = tmp_path / "k.pem"
    key_path.write_bytes(private)
    out_dir = tmp_path / "workdir"
    out_dir.mkdir()
    missing = tmp_path / "no-such.dmg"
    result = _run(BUILD_TOOL, "--payload-dir", str(payload), "--version", "9.9.9",
                  "--platform", "macos", "--edition", "full",
                  "--asset", f"macos-full={missing}", "--key", str(key_path),
                  cwd=out_dir)
    assert result.returncode != 0
    assert "不存在" in (result.stdout + result.stderr)
    assert not (out_dir / feed_filename("macos", "full")).exists()

    # 换一个存在的资产再跑，验证默认名
    real = tmp_path / "OmniCrawler-9.9.9-macOS-Portable-Full.dmg"
    real.write_bytes(b"dmg")
    ok = _run(BUILD_TOOL, "--payload-dir", str(payload), "--version", "9.9.9",
              "--platform", "macos", "--edition", "full",
              "--asset", f"macos-full={real}", "--key", str(key_path), cwd=out_dir)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert (out_dir / feed_filename("macos", "full")).is_file()


def test_notes_file_is_read_as_utf8(tmp_path: Path) -> None:
    """摘要走文件（UTF-8）—— 绕开 Windows PS 5.1 的 argv 转码（中文会变 ??）。"""
    payload = _payload(tmp_path)
    notes = tmp_path / "notes.txt"
    notes.write_text("中文摘要：修复了若干问题\n", encoding="utf-8")
    out = tmp_path / "u.json"
    ok = _run(BUILD_TOOL, "--payload-dir", str(payload), "--version", "9.9.9",
              "--notes-file", str(notes), "--emit-unsigned", str(out))
    assert ok.returncode == 0, ok.stdout + ok.stderr
    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["notes"] == "中文摘要：修复了若干问题"


# ── 一条命令签完 (平台 × 版本) 全网格 ──────────────────────────────────────


def _unsigned_body(tmp_path: Path, *, platform: str, edition: str) -> bytes:
    """产一份可被网格签名的无签名清单字节。"""
    payload = _payload(tmp_path / f"p-{platform}-{edition}")
    out = tmp_path / f"u-{platform}-{edition}.json"
    asset = tmp_path / f"OmniCrawler-9.9.9-{platform}-{edition}.bin"
    asset.write_bytes(b"pkg")
    result = _run(BUILD_TOOL, "--payload-dir", str(payload), "--version", "9.9.9",
                  "--platform", platform, "--edition", edition,
                  "--asset", f"{platform}-{edition}={asset}",
                  "--emit-unsigned", str(out))
    assert result.returncode == 0, result.stdout + result.stderr
    return out.read_bytes()


class _Resp:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _grid_fake_urlopen(bodies: dict[str, bytes]):
    import urllib.error

    def _open(url: str, timeout: int = 0) -> _Resp:
        if url not in bodies:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]
        return _Resp(bodies[url])

    return _open


def test_signer_grid_signs_all_platform_edition_combinations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 每版发布的那一条命令：``--platforms × --editions`` 一次签完，文件名是客户端会找的。"""
    module = _load_module("sign_update_manifest", SIGN_TOOL)
    private, public = signing.generate_keypair()
    key_path = tmp_path / "k.pem"
    key_path.write_bytes(private)
    base = "https://example.com/download"
    bodies = {
        f"{base}/update-{p}-{e}.unsigned.json": _unsigned_body(tmp_path, platform=p, edition=e)
        for p in ("windows", "linux", "macos")
        for e in ("standard", "full")
    }
    monkeypatch.setattr(module.urllib.request, "urlopen", _grid_fake_urlopen(bodies))
    out_dir = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", [
        "sign_update_manifest.py",
        "--unsigned-url-base", base,
        "--platforms", "windows,linux,macos",
        "--editions", "standard,full",
        "--key", str(key_path),
        "--out-dir", str(out_dir),
    ])
    assert module.main() == 0

    produced = sorted(path.name for path in out_dir.iterdir())
    assert produced == sorted(
        f"update-{p}-{e}.json" for p in ("windows", "linux", "macos")
        for e in ("standard", "full")
    )
    # 签出来的每一份都要能被客户端验签通过（平台/版本字段也要跟着传下去）
    for path in out_dir.iterdir():
        feed = verify_feed_document(
            path.read_bytes(), trusted_public_key=decode_public_key(public.decode())
        )
        assert feed.version == "9.9.9"
        assert feed.platform in {"windows", "linux", "macos"}
        assert feed.edition in {"standard", "full"}


def test_signer_grid_skips_missing_but_keeps_going(tmp_path: Path, monkeypatch) -> None:
    """某平台某版本没随发布（超 2GiB 等已知情形）⇒ 跳过它、其余照签，不整体失败。"""
    module = _load_module("sign_update_manifest", SIGN_TOOL)
    private, _public = signing.generate_keypair()
    key_path = tmp_path / "k.pem"
    key_path.write_bytes(private)
    base = "https://example.com/download"
    bodies = {
        f"{base}/update-linux-standard.unsigned.json": _unsigned_body(
            tmp_path, platform="linux", edition="standard"
        ),
    }
    monkeypatch.setattr(module.urllib.request, "urlopen", _grid_fake_urlopen(bodies))
    out_dir = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", [
        "sign_update_manifest.py", "--unsigned-url-base", base,
        "--platforms", "windows,linux", "--editions", "standard",
        "--key", str(key_path), "--out-dir", str(out_dir),
    ])
    assert module.main() == 0
    assert [p.name for p in out_dir.iterdir()] == ["update-linux-standard.json"]


def test_signer_grid_strict_requires_every_manifest(tmp_path: Path, monkeypatch) -> None:
    """``--strict`` ⇒ 缺一份就红（给"确认全都在"的场合用）。"""
    module = _load_module("sign_update_manifest", SIGN_TOOL)
    private, _public = signing.generate_keypair()
    key_path = tmp_path / "k.pem"
    key_path.write_bytes(private)
    base = "https://example.com/download"
    monkeypatch.setattr(
        module.urllib.request, "urlopen",
        _grid_fake_urlopen({
            f"{base}/update-linux-standard.unsigned.json": _unsigned_body(
                tmp_path, platform="linux", edition="standard"
            )
        }),
    )
    monkeypatch.setattr(sys, "argv", [
        "sign_update_manifest.py", "--unsigned-url-base", base,
        "--platforms", "windows,linux", "--editions", "standard", "--strict",
        "--key", str(key_path), "--out-dir", str(tmp_path / "out"),
    ])
    assert module.main() == 2


def test_signer_grid_fails_on_corrupt_manifest_not_skip(tmp_path: Path, monkeypatch) -> None:
    """★ 反向断言：**有但坏了**必须报错，不能当"缺席"跳过。

    跳过与报错的边界只能落在 HTTP 状态码上；用错误文案里有没有 "404" 去猜，一次措辞
    改动就会把边界反过来 —— 那正是"看起来成功、其实少签了一份"的来源。
    """
    module = _load_module("sign_update_manifest", SIGN_TOOL)
    private, _public = signing.generate_keypair()
    key_path = tmp_path / "k.pem"
    key_path.write_bytes(private)
    base = "https://example.com/download"
    monkeypatch.setattr(
        module.urllib.request, "urlopen",
        _grid_fake_urlopen({f"{base}/update-linux-standard.unsigned.json": b"not json {{{"}),
    )
    monkeypatch.setattr(sys, "argv", [
        "sign_update_manifest.py", "--unsigned-url-base", base,
        "--platforms", "linux", "--editions", "standard",
        "--key", str(key_path), "--out-dir", str(tmp_path / "out"),
    ])
    assert module.main() == 2


def test_is_not_found_only_trusts_http_status_and_file_absence(tmp_path: Path) -> None:
    """``_is_not_found`` 的判据只看状态码/文件缺失，不看错误文案。"""
    import urllib.error

    module = _load_module("sign_update_manifest", SIGN_TOOL)
    not_found = ValueError("x")
    not_found.__cause__ = urllib.error.HTTPError("u", 404, "nf", {}, None)  # type: ignore[arg-type]
    assert module._is_not_found(not_found) is True

    server_error = ValueError("x")
    server_error.__cause__ = urllib.error.HTTPError("u", 500, "err", {}, None)  # type: ignore[arg-type]
    assert module._is_not_found(server_error) is False

    bad_json = ValueError("无签名清单不是合法 JSON: ...404...")
    assert module._is_not_found(bad_json) is False, "文案里出现 404 不算缺席"

    missing = ValueError("无签名清单不存在: x")
    missing.__cause__ = FileNotFoundError("x")
    assert module._is_not_found(missing) is True
