"""`source_base`：**索引只背书、不搬运字节**（#77 设计要点 3）。

为什么必须钉住：设计里写着「官方索引条目可带 `source_base`（指向创作者仓库），**保留创作者签名**不变；
索引只背书、不改创作者字节」，而**全仓此前零实现**（`grep source_base` 无命中）。
后果不是"少个高级功能"，而是"社区/主题索引聚合他人仓库"在实际安装时**必然失败**：
客户端会去**索引自己的基址**找包（那里根本没有），或更糟 —— 索引基址恰好有同名文件时**取错字节**。

安全边界（跟索引给的基址去取包为什么可以）：
① 包字节仍要用**创作者签名**验；② 仍要对清单固化的 sha256；③ 目标主机仍过 egress 策略。
即：索引能决定"去哪儿取"，**决定不了"取到的东西算不算数"**。

★ 本文件**自包含**（两个本地目录：索引 + 创作者仓库），不依赖兄弟仓 clone ⇒ 任何环境都会真跑。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omnicrawler.plugins import market_client, signing

pytestmark = pytest.mark.skipif(
    not hasattr(signing, "sign_bytes"), reason="签名工具不可用"
)


def _keypair() -> tuple[bytes, bytes]:
    private_pem, public_pem = signing.generate_keypair()
    return private_pem, public_pem


def _write_index(root: Path, private_pem: bytes, entry: dict[str, object]) -> Path:
    """造一个**已签名**的索引目录（只放 catalog，不放包）。"""
    root.mkdir(parents=True, exist_ok=True)
    catalog = {"schema_version": 1, "plugins": [entry]}
    raw = json.dumps(catalog, ensure_ascii=False).encode("utf-8")
    (root / "catalog.json").write_bytes(raw)
    (root / "catalog.json.sig").write_bytes(signing.sign_bytes(raw, private_pem))
    return root


def _creator_repo(root: Path, private_pem: bytes, body: bytes) -> Path:
    """造一个创作者仓库目录（只有包与签名，**没有 catalog**）。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / "plugin.py").write_bytes(body)
    (root / "plugin.py.sig").write_bytes(signing.sign_bytes(body, private_pem))
    return root


def _entry(**overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "id": "demo_creator",
        "name": "demo_creator",
        "version": "1.0.0",
        "publisher": "alice",
        "plugin_file": "plugin.py",
        "signature_file": "plugin.py.sig",
        "signature_algorithm": "ed25519",
    }
    entry.update(overrides)
    return entry


def test_entry_source_base_prefers_the_entry_then_falls_back() -> None:
    """判据本体：条目声明优先；空白/缺失回落索引基址（不能因为一个空串就去别的域名取东西）。"""
    assert market_client.entry_source_base(
        {"source_base": "https://creator.example/repo"}, catalog_url="https://index.example"
    ) == "https://creator.example/repo"
    assert market_client.entry_source_base(
        {"source_base": "   "}, catalog_url="https://index.example"
    ) == "https://index.example"
    assert market_client.entry_source_base({}, catalog_url="/local/index") == "/local/index"


def test_install_fetches_bytes_from_the_creators_repo(tmp_path: Path) -> None:
    """★ 正向：索引**只背书**（目录里刻意不放包）⇒ 装成功就只能是走了 `source_base`。"""
    private_pem, public_pem = _keypair()
    trust = tmp_path / "trust.pem"
    trust.write_bytes(public_pem)

    body = b"# creator plugin\n"
    creator = _creator_repo(tmp_path / "creator-repo", private_pem, body)
    index = _write_index(
        tmp_path / "index", private_pem, _entry(source_base=str(creator))
    )
    assert not (index / "plugin.py").exists(), "索引目录里不该有包（否则本用例证明不了什么）"

    path = market_client.download_and_verify(
        "demo_creator", str(index), tmp_path / "installed", str(trust)
    )

    assert path.read_bytes() == body
    assert (path.parent / "plugin.py.sig").is_file()


def test_install_fails_when_source_base_is_absent_and_index_has_no_bytes(tmp_path: Path) -> None:
    """★ 反向：**不给** `source_base` ⇒ 只能在索引基址找 ⇒ 找不到就**明确失败**。

    这一条与上一条配对：若实现里根本没有读 `source_base`，上一条会失败；
    若实现变成"到处乱找"，这一条会失败。两条一起才把行为钉死。
    """
    private_pem, public_pem = _keypair()
    trust = tmp_path / "trust.pem"
    trust.write_bytes(public_pem)

    _creator_repo(tmp_path / "creator-repo", private_pem, b"# creator plugin\n")
    index = _write_index(tmp_path / "index", private_pem, _entry())

    with pytest.raises(Exception) as excinfo:
        market_client.download_and_verify(
            "demo_creator", str(index), tmp_path / "installed", str(trust)
        )

    assert "plugin.py" in str(excinfo.value), "报错要指出缺哪个文件，而不是一句笼统失败"


def test_source_base_does_not_relax_signature_verification(tmp_path: Path) -> None:
    """★★ 关键的安全断言：换了取包的地方，**创作者签名照样要验**。

    如果 `source_base` 的实现顺手绕过了验签（比如"第三方基址就不校验了"），这一条会红。
    """
    private_pem, public_pem = _keypair()
    trust = tmp_path / "trust.pem"
    trust.write_bytes(public_pem)

    # 创作者仓库里的包**被篡改**（签名对不上）
    creator = tmp_path / "creator-repo"
    creator.mkdir()
    (creator / "plugin.py").write_bytes(b"# TAMPERED\n")
    (creator / "plugin.py.sig").write_bytes(signing.sign_bytes(b"# original\n", private_pem))
    index = _write_index(tmp_path / "index", private_pem, _entry(source_base=str(creator)))

    with pytest.raises(PermissionError):
        market_client.download_and_verify(
            "demo_creator", str(index), tmp_path / "installed", str(trust)
        )
