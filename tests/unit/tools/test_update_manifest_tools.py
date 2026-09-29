"""CI 产无签名清单 + 本机只签名（#A.12 改进）的端到端与反向断言。

为什么要这条流水线：逐文件哈希需要一个**已解压载荷目录**才算得出，过去只能在维护者本机做
⇒ 每版发布都要搬 515MB。拆开之后 CI 只算哈希（无密钥），本机只签名 ⇒ 每次发布一条命令。
本文件钉住三件事：① 拆分后**端到端仍然验签通过**；② **无签名清单不可发布**；
③ 误把已签名清单再签一次会被拒绝。
"""

from __future__ import annotations

import json
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


def _run(tool: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(tool), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=REPO_ROOT,
    )


def _payload(tmp_path: Path) -> Path:
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "app.exe").write_bytes(b"hello")
    (payload / "lib.dll").write_bytes(b"world")
    return payload


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
