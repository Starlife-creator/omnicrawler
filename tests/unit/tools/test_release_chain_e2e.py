"""发布链路端到端：签名 → 信任 → 安装 → 发现 → 加载 → 功能可用。

## 这条测试证明什么

前面几轮的守卫各自只覆盖一段：

| 守卫 | 覆盖 |
|---|---|
| `test_facade_entry_contracts` | 装了之后对象对不对 |
| `test_facade_operation_routing` | 参数送没送到 |
| `test_facade_uninstalled_contracts` | 没装时可操作 |
| `test_facade_signature_gate` | 未签名拒载 |
| `test_plugin_dir_layouts` | 三层布局能发现 |

但**没有一条**把"用户从市场装一个插件"的完整路径跑通。
这段链条任何一环断了，用户看到的就是"装好了但用不了"。

## 用测试身份，不碰真实私钥

全程临时目录 + `IdentityStore.create()`，绝不读取
`C:\\Users\\Lenovo\\Desktop\\档案` 下的维护者私钥。

## 关键判据

1. **未信任创作者** ⇒ `CreatorUntrusted`（这不是失败，是设计：
   首次安装必须由用户显式信任）；
2. **用户信任后** ⇒ `CreatorTrusted`；
3. 按市场三层布局安装后，facade 能发现；
4. 加载后的插件**功能真的可用**（selfcheck / 表格格式 / GUI 视图）。

第 1 条常被误解为"签名没用"——恰恰相反，它说明"签名有效"与
"这台机器信任该作者"是两道独立的门。
"""

from __future__ import annotations

import base64
import json
import shutil
from pathlib import Path

import pytest

from omnicrawler.plugins import trust as trust_model
from omnicrawler.plugins.identity import IdentityStore
from omnicrawler.plugins.package_manifest import sign_creator_package

PLUGIN_SRC = (
    Path(__file__).resolve().parents[4]
    / "OmniCrawler-market"
    / "submissions"
    / "plugins"
    / "4c3014804d85de2568f85e62a3048429"
    / "document-workbench"
)


class _FakeKeyring:
    def __init__(self) -> None:
        self.data: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.data.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.data[(service, username)] = password


def _public_pem(raw: bytes) -> str:
    return (
        "-----BEGIN PUBLIC KEY-----\n"
        + base64.b64encode(raw).decode("ascii")
        + "\n-----END PUBLIC KEY-----\n"
    )


@pytest.fixture
def signed_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """造一个已签名的插件包 + 一个可写的默认信任列表。"""
    if not PLUGIN_SRC.is_dir():
        pytest.skip(f"找不到插件源码：{PLUGIN_SRC}")

    from omnicrawler.core.secrets_store import SecretsStore

    secrets = SecretsStore(tmp_path / "secrets.bin", keyring_api=_FakeKeyring())
    identity = IdentityStore(store=secrets).create("tester", "pw")

    package_dir = tmp_path / "document-workbench"
    shutil.copytree(
        PLUGIN_SRC, package_dir,
        # 必须忽略**测试运行产物**：插件仓里的 pytest 临时目录会含 `.env`
        # 之类文件，而 `_payload_files` 把它们判为"疑似凭据"直接拒签
        # （实测：`pytest-of-Lenovo/.../proj/.env` 导致签名失败）。
        ignore=shutil.ignore_patterns(
            "__pycache__", ".pytest_cache", "work", "output",
            "pytest-of-*", "tmp*", ".gitignore",
        ),
    )
    signed = sign_creator_package(
        package_dir,
        package_type="plugin",
        package_id="document-workbench",
        version="1.0.0",
        identity=identity,
        legacy_target="plugin.py",
    )

    trust_root = tmp_path / "creator.pub.pem"
    trust_root.write_text(
        _public_pem(identity.export_identity().public_key), encoding="utf-8"
    )

    # 真实用户流程用默认信任列表；沙箱不能建 ~/.omnicrawler，改指向临时文件
    monkeypatch.setattr(trust_model, "DEFAULT_TRUST_LIST", tmp_path / "trusted_users.json")

    return {
        "package_dir": package_dir,
        "identity": identity,
        "trust_root": trust_root,
        "manifest_sha256": signed.manifest_sha256,
    }


def test_signature_artifacts_are_generated(signed_package: dict) -> None:
    """签名必须产出校验器要求的三个文件，且 manifest 不含测试残留。"""
    package_dir: Path = signed_package["package_dir"]
    for name in ("creator.identity", "package.manifest.json", "package.manifest.creator.sig"):
        assert (package_dir / name).is_file(), f"缺签名产物: {name}"

    manifest = json.loads((package_dir / "package.manifest.json").read_text(encoding="utf-8"))
    files = set(manifest["files"])
    assert "plugin.py" in files
    leaked = [f for f in files if "pytest" in f or f.endswith(".sqlite3") or "/work/" in f]
    assert not leaked, f"manifest 含运行产物（会被算进包哈希）: {leaked}"


def test_untrusted_creator_is_valid_but_not_trusted(signed_package: dict) -> None:
    """签名有效但创作者未受信 ⇒ CreatorUntrusted。

    这不是缺陷：首次安装必须由用户显式信任。把这条测出来，
    是为了防止有人"为了跑通"把信任列表检查删掉。
    """
    decision = trust_model.verify_plugin_trust(
        signed_package["package_dir"],
        str(signed_package["trust_root"]),
        trust_model.TrustedUserList(),
    )
    assert decision.level == trust_model.TrustLevel.CreatorUntrusted
    assert decision.creator is not None, "签名无效时不会带 creator —— 说明签名其实是好的"


def test_trusted_creator_passes(signed_package: dict) -> None:
    """用户显式信任后 ⇒ CreatorTrusted。"""
    trusted = trust_model.TrustedUserList()
    trusted.add(signed_package["identity"].export_identity(), source="test", path_hint="e2e")

    decision = trust_model.verify_plugin_trust(
        signed_package["package_dir"],
        str(signed_package["trust_root"]),
        trusted,
    )
    assert decision.level == trust_model.TrustLevel.CreatorTrusted


def test_full_release_chain_ends_in_working_plugin(
    signed_package: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """签名 → 信任 → 三层安装 → facade 发现 → 加载 → 功能可用。"""
    identity = signed_package["identity"]
    package_dir: Path = signed_package["package_dir"]

    # 用户信任该创作者
    trusted = trust_model.TrustedUserList()
    trusted.add(identity.export_identity(), source="test", path_hint="e2e")

    # 按市场安装布局（三层）放到 plugins_installed
    target = (
        tmp_path / "plugins_installed"
        / identity.key_fingerprint
        / "document-workbench"
        / "1.0.0"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(package_dir, target)

    # 让 facade 走真实验签路径（清掉开发逃生口）
    monkeypatch.delenv("OMNICRAWL_PLUGIN_SIGNATURE_POLICY", raising=False)
    monkeypatch.setenv("OMNICRAWL_PDF_PLUGIN_DIR", str(target))

    from omnicrawler.services import document_facade

    assert document_facade._plugin_dir() is not None, "三层布局未被发现"

    # 插件能加载且功能可用
    report = document_facade.run_operation("pdf.selfcheck", {})
    assert report.get("ok") is True, f"selfcheck 失败: {report}"

    table_io = document_facade.table_io()
    # 7 种 = csv / jsonl+ndjson / parquet / duckdb+db / xlsx（别名算两项）。
    # 表格读写已上提到宿主，插件的 table_io 入口返回宿主实现。
    assert len(table_io.READERS) >= 5, f"表格格式过少: {len(table_io.READERS)}"

    views = document_facade.pdf_views()
    assert hasattr(views, "DocumentWorkbenchView")
    assert hasattr(views, "ConvertView")
