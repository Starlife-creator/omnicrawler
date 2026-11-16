"""组件签名工具：规范化必须与客户端验签**逐字一致**。

**为什么这条最要命**

签名覆盖的是"规范化后的清单字节"。签名工具与客户端如果各写一份规范化，
差一个空格、差一次 `sort_keys`、差一个 `ensure_ascii`，**签名就永远验不过**——
而且报错是"签名校验失败"，看起来像钥匙搞错了，实际是编码不一致。
这类 bug 极难排查，所以必须用同一个函数、并由测试钉住。

**测试环境的两个约束**（实测踩过，记录以免重复）

1. 本仓 pytest 的临时根被配置在**仓库内部**；`LOCALAPPDATA\\Temp` 在这台机器上
   访问会阻塞（`mkdtemp` 卡住 ≥30 秒）。所以测试**不能用**"物理上在仓外的临时目录"
   来绕过私钥判据。
2. 正确做法：工具支持 `OMNICRAWL_SIGN_REPO_ROOT` 覆盖"仓界"（生产不设该变量，
   行为不变），测试把仓界指到一个假的 repo 目录即可。判据本身不被放宽。
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest


def _make_package(tmp_path: Path, *, tamper: bool = False) -> Path:
    """造一个自制组件包（可选手脚改哈希）。"""
    binary = b"fake-ocr-binary"
    digest = hashlib.sha256(binary).hexdigest()
    if tamper:
        digest = "0" * 64  # 故意写错哈希
    manifest = {
        "name": "ocr-demo",
        "version": "1.0.0",
        "files": {"runtime/ocr.exe": digest},
        "disk_bytes": len(binary),
        "runtime": {
            "protocol": "ocr-file-v1",
            "engine": "tesseract",
            "entrypoint": "runtime/ocr.exe",
            "model_license": "Apache-2.0",
        },
    }
    package = tmp_path / "ocr-demo.ocp"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("component.json", json.dumps(manifest))
        archive.writestr("runtime/ocr.exe", binary)
    return package


@pytest.fixture()
def signing_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """准备一对密钥与一个"假仓库根"，让私钥位于仓界之外。"""
    from omnicrawler.plugins.signing import generate_keypair

    private_pem, public_pem = generate_keypair()
    fake_repo = tmp_path / "fake-repo"
    fake_repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    private_path = outside / "private.pem"
    private_path.write_bytes(private_pem)

    monkeypatch.setenv("OMNICRAWL_SIGN_REPO_ROOT", str(fake_repo))
    return private_path, public_pem


def test_canonical_bytes_match_client_verification() -> None:
    """签名工具的规范化必须与客户端验签侧**逐字相同**。"""
    from tools.sign_component import canonical_manifest, strip_signature

    raw = {
        "name": "ocr-demo",
        "version": "1.0.0",
        "files": {"b": "x" * 64, "a": "y" * 64},
        "purpose": "含中文的说明",
        "signature": "should-be-stripped",
    }
    body = strip_signature(raw)
    tool_bytes = canonical_manifest(body)

    # 客户端侧的同构实现（照抄 inspect_package 的那一行）
    client_bytes = json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    assert tool_bytes == client_bytes


def test_signed_package_passes_client_inspection(
    tmp_path: Path, signing_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """端到端：工具签出的包必须能被客户端 `inspect_package` 接受。

    这是本工具存在的**唯一理由**——签出来装不进去等于没写。
    """
    import argparse

    from omnicrawler.services.component_manager import ComponentManager
    from omnicrawler.services.update_feed import decode_public_key
    from tools.sign_component import command_sign

    private_path, public_pem = signing_env
    package = _make_package(tmp_path)
    command_sign(argparse.Namespace(package=str(package), private_key=str(private_path)))

    manager = ComponentManager(
        tmp_path / "components",
        trusted_public_key=decode_public_key(public_pem.decode()),
    )
    info = manager.inspect_package(package)
    assert info.name == "ocr-demo"
    assert info.runtime["engine"] == "tesseract"


def test_tampered_hash_is_refused_before_signing(tmp_path: Path, signing_env) -> None:
    """哈希对不上时必须**拒绝签名**，而不是签一个坏包。"""
    import argparse

    from tools.sign_component import command_sign

    private_path, _ = signing_env
    package = _make_package(tmp_path, tamper=True)

    with pytest.raises(SystemExit) as excinfo:
        command_sign(argparse.Namespace(package=str(package), private_key=str(private_path)))
    assert ("哈希" in str(excinfo.value)) or ("自检" in str(excinfo.value))


def test_private_key_inside_repo_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """私钥在**仓界内**必须被拒绝（冷密钥纪律）。

    通过 `OMNICRAWL_SIGN_REPO_ROOT` 把仓界指到一个假 repo，私钥放在它下面。
    """
    import argparse

    from omnicrawler.plugins.signing import generate_keypair
    from tools.sign_component import command_sign

    private_pem, _ = generate_keypair()
    fake_repo = tmp_path / "fake-repo"
    (fake_repo / "keys").mkdir(parents=True)
    inside = fake_repo / "keys" / "private.pem"
    inside.write_bytes(private_pem)
    monkeypatch.setenv("OMNICRAWL_SIGN_REPO_ROOT", str(fake_repo))

    package = _make_package(tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        command_sign(argparse.Namespace(package=str(package), private_key=str(inside)))
    assert ("仓库内" in str(excinfo.value)) or ("仓外" in str(excinfo.value))


def test_inspect_is_read_only(tmp_path: Path) -> None:
    """inspect 不得修改包内容。"""
    import argparse

    from tools.sign_component import command_inspect

    package = _make_package(tmp_path)
    before = hashlib.sha256(package.read_bytes()).hexdigest()
    command_inspect(argparse.Namespace(package=str(package)))
    after = hashlib.sha256(package.read_bytes()).hexdigest()
    assert before == after


def test_signature_covers_all_fields_except_signature(
    tmp_path: Path, signing_env
) -> None:
    """签名必须覆盖清单除 signature 外的**全部**字段。

    判据：改任何一个字段（哪怕是不起眼的 purpose），验签都必须失败。
    """
    import argparse

    from omnicrawler.services.component_manager import ComponentManager
    from omnicrawler.services.update_feed import decode_public_key
    from tools.sign_component import command_sign

    private_path, public_pem = signing_env
    package = _make_package(tmp_path)
    command_sign(argparse.Namespace(package=str(package), private_key=str(private_path)))

    # 拆包，改一个字段，再原样打回去（签名不变）
    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
        payloads = {name: archive.read(name) for name in names}
    manifest = json.loads(payloads["component.json"])
    manifest["purpose"] = "被篡改的用途"
    payloads["component.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(package, "w") as archive:
        for name, payload in payloads.items():
            archive.writestr(name, payload)

    manager = ComponentManager(
        tmp_path / "components",
        trusted_public_key=decode_public_key(public_pem.decode()),
    )
    with pytest.raises(Exception) as excinfo:
        manager.inspect_package(package)
    message = str(excinfo.value)
    assert message.strip(), "篡改后验签失败但错误消息为空"
    assert "签名" in message
