"""插件本地签名、上传包生成与本地目录扫描（GUI 生态闭环核心）。

- ``sign_plugin_local``   ：本地一键签名（creator-sign + 自动加入本地信任列表），
  等价于 ``tools/sign_plugin.py local-sign``，供 GUI 直接调用；
- ``build_plugin_upload`` ：生成提交市场仓库的 PR 文件集（plugin.py /
  creator.sig / creator.identity / plugin.yaml / listing.md /
  keys/<pem指纹>.pub.pem / authors/<username>.yaml）；
- ``build_template_upload``：模板同构（template.yaml 注入 template: 元数据块）；
- ``scan_local_plugins``  ：扫描 plugins/ 目录，把条目分为
  已签名且属于当前用户 / 已签名未信任 / 已签名已信任 / 未签名 四态。

指纹双轨约定：
- 客户端身份指纹 = SHA-256(公钥原始字节) 前 16 字节 hex（identity.py）；
- 市场作者指纹     = SHA-256(PEM 文件字节) 前 16 字节 hex（generate_catalog.py）。
  上传包同时携带两者：``creator.identity`` 用客户端指纹，``authors/*.yaml``
  与 ``keys/*.pub.pem`` 用市场指纹。
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .identity import IdentityStore, UserIdentity
from .trust import TrustedUserList

_PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_TEMPLATE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]*(?:/[a-z0-9_-]+)*$")


class PackagingError(ValueError):
    """插件打包/签名流程错误（fail-closed，消息面向用户）。"""


@dataclass(frozen=True, slots=True)
class LocalPluginEntry:
    """本地目录中的一个插件条目（四态分类结果）。"""

    path: Path
    name: str
    version: str
    description: str
    status: str  # signed_by_me | signed_untrusted | signed_trusted | unsigned
    author_username: str = ""
    fingerprint: str = ""


def _load_user(username: str, password: str) -> UserIdentity:
    try:
        return IdentityStore().load(username, password)
    except Exception as exc:  # noqa: BLE001 - 身份库错误统一转可读异常
        raise PackagingError(f"身份加载失败：{exc}") from exc


def _pem_fingerprint(pem_bytes: bytes) -> str:
    """市场作者指纹：SHA-256(PEM 字节) 前 16 字节 hex（生态唯一标识）。

    先归一化行尾（\\r\\n → \\n）：市场侧 tools/generate_catalog.py 对检出文件
    做同样归一化，避免 Windows（CRLF）与 CI（LF）对同一公钥算出两个指纹。
    """
    return hashlib.sha256(pem_bytes.replace(b"\r\n", b"\n")).hexdigest()[:32]


def _public_pem(user: UserIdentity) -> bytes:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    creator = user.export_identity()
    public = Ed25519PublicKey.from_public_bytes(creator.public_key)
    return public.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


def _read_metadata(plugin_file: Path) -> dict[str, Any]:
    """AST 静态读取 PLUGIN_METADATA（不执行插件代码）。"""
    try:
        tree = ast.parse(plugin_file.read_text(encoding="utf-8"), filename=str(plugin_file))
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise PackagingError(f"无法解析插件源码：{exc}") from exc
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(t, ast.Name) and t.id == "PLUGIN_METADATA" for t in targets):
            continue
        try:
            value = ast.literal_eval(node.value) if node.value is not None else {}
        except (ValueError, TypeError) as exc:
            raise PackagingError("PLUGIN_METADATA 必须是静态字面量") from exc
        if isinstance(value, dict):
            return value
        raise PackagingError("PLUGIN_METADATA 必须是字典")
    return {}


def sign_plugin_local(plugin_dir: Path, *, username: str, password: str, target: str = "plugin.py") -> str:
    """本地一键签名：creator.sig + creator.identity + 自动加入信任列表。

    返回客户端身份指纹（显示用）。
    """
    plugin_dir = plugin_dir.resolve()
    target_path = plugin_dir / target
    if not target_path.is_file():
        raise PackagingError(f"缺少待签名文件 {target}: {target_path}")
    user = _load_user(username, password)
    creator = user.export_identity()
    (plugin_dir / "creator.sig").write_bytes(user.sign_bytes(target_path.read_bytes()))
    (plugin_dir / "creator.identity").write_text(
        json.dumps(creator.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    TrustedUserList().add(creator, source="local", path_hint=f"（{plugin_dir}）")
    return creator.key_fingerprint


def _plugin_id_from_dir(plugin_dir: Path) -> str:
    plugin_id = plugin_dir.name
    if not _PLUGIN_ID_RE.match(plugin_id):
        raise PackagingError(
            f"插件目录名 {plugin_id!r} 不能作为市场 ID：须为小写字母开头的 "
            "2-64 位小写字母/数字/下划线/短横线（如 my_plugin）"
        )
    return plugin_id


def build_plugin_upload(
    plugin_dir: Path,
    *,
    username: str,
    password: str,
    listing: str,
) -> dict[str, bytes]:
    """生成提交市场仓库的插件 PR 文件集（相对路径 -> 内容）。

    插件目录必须已由当前用户签名（creator.sig + creator.identity 指纹匹配）。
    返回的文件集写入市场仓库：
      plugins/<id>/plugin.py / creator.sig / creator.identity / plugin.yaml / listing.md
      keys/<pem指纹>.pub.pem
      authors/<username>.yaml（首次上传）
    """
    plugin_dir = plugin_dir.resolve()
    plugin_file = plugin_dir / "plugin.py"
    if not plugin_file.is_file():
        raise PackagingError(f"缺少 plugin.py: {plugin_file}")
    identity_path = plugin_dir / "creator.identity"
    sig_path = plugin_dir / "creator.sig"
    if not identity_path.is_file() or not sig_path.is_file():
        raise PackagingError("请先在 GUI 中对该插件执行签名（私人栏 → 签名）")

    user = _load_user(username, password)
    creator = user.export_identity()
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    if identity.get("key_fingerprint") != creator.key_fingerprint:
        raise PackagingError("插件签名身份与当前用户不一致：请用创建该签名的身份上传")

    plugin_id = _plugin_id_from_dir(plugin_dir)
    metadata = _read_metadata(plugin_file)
    name = str(metadata.get("name") or plugin_id)
    version = str(metadata.get("version") or "0.1.0")
    category = str((metadata.get("plugin_types") or ["source"])[0])
    summary = str(metadata.get("description") or "")
    if not summary:
        raise PackagingError("插件缺少介绍：请在 PLUGIN_METADATA 中填写 description")

    pem = _public_pem(user)
    pem_fingerprint = _pem_fingerprint(pem)

    manifest = {
        "id": plugin_id,
        "name": name,
        "version": version,
        "publisher": username,
        "author_fingerprint": pem_fingerprint,
        "category": category,
        "summary": summary,
        "description_file": f"plugins/{plugin_id}/listing.md",
        "plugin_file": f"plugins/{plugin_id}/plugin.py",
        "signature_file": f"plugins/{plugin_id}/plugin.py.sig",
        "signature_algorithm": "ed25519",
        "permissions": list(metadata.get("permissions") or []),
        "compatible_core": f">={metadata.get('min_core_version') or '1.0.0'}",
        "license": str(metadata.get("license") or "MIT"),
        "tags": list(metadata.get("tags") or []),
        "updated_at": date.today().isoformat(),
    }
    import yaml

    files: dict[str, bytes] = {
        f"plugins/{plugin_id}/plugin.py": plugin_file.read_bytes(),
        f"plugins/{plugin_id}/creator.sig": sig_path.read_bytes(),
        f"plugins/{plugin_id}/creator.identity": identity_path.read_bytes(),
        f"plugins/{plugin_id}/plugin.yaml": (
            "# 插件清单（由 OmniCrawler 生成；CI 校验 author_fingerprint 与公钥指纹）\n"
            + yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False)
        ).encode("utf-8"),
        f"plugins/{plugin_id}/listing.md": listing.encode("utf-8"),
        f"keys/{pem_fingerprint}.pub.pem": pem,
        f"authors/{username}.yaml": (
            "# 作者记录（首次上传自动生成；指纹 = SHA-256(PEM 公钥字节) 前 16 字节 hex）\n"
            f"username: {username}\n"
            f"display_name: {username}\n"
            f"pubkey_ref: ../keys/{pem_fingerprint}.pub.pem\n"
            f"fingerprint: {pem_fingerprint}\n"
            "roles: [publisher]\n"
        ).encode(),
    }
    return files


def build_template_upload(
    template_dir: Path,
    *,
    username: str,
    password: str,
    template_id: str,
    name: str,
    version: str,
    category: str,
    summary: str,
    listing: str,
) -> dict[str, bytes]:
    """模板上传包：template.yaml 注入 template: 市场元数据块后签名并生成文件集。"""
    template_dir = template_dir.resolve()
    template_file = template_dir / "template.yaml"
    if not template_file.is_file():
        raise PackagingError(f"缺少 template.yaml: {template_file}")
    if not _TEMPLATE_ID_RE.match(template_id) or ".." in template_id:
        raise PackagingError(f"非法模板 ID: {template_id}")
    if not name.strip() or not summary.strip():
        raise PackagingError("模板名称与简介不能为空")

    import yaml

    raw = yaml.safe_load(template_file.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise PackagingError("template.yaml 必须是映射")
    raw["template"] = {
        "id": template_id,
        "name": name,
        "version": version,
        "category": category,
        "description": summary,
        "publisher": username,
        "author_fingerprint": "0" * 32,  # 占位：签名后由下方真实指纹覆盖
        "min_core_version": "1.0.0",
        "license": "MIT",
    }

    user = _load_user(username, password)
    creator = user.export_identity()
    pem = _public_pem(user)
    pem_fingerprint = _pem_fingerprint(pem)
    raw["template"]["author_fingerprint"] = pem_fingerprint
    content = yaml.safe_dump(raw, allow_unicode=True, sort_keys=False).encode("utf-8")

    files: dict[str, bytes] = {
        f"templates/{template_id}/template.yaml": content,
        f"templates/{template_id}/template.yaml.sig": user.sign_bytes(content),
        f"templates/{template_id}/creator.sig": user.sign_bytes(content),
        f"templates/{template_id}/creator.identity": (
            json.dumps(creator.to_dict(), ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8"),
        f"templates/{template_id}/listing.md": listing.encode("utf-8"),
        f"keys/{pem_fingerprint}.pub.pem": pem,
        f"authors/{username}.yaml": (
            f"username: {username}\n"
            f"display_name: {username}\n"
            f"pubkey_ref: ../keys/{pem_fingerprint}.pub.pem\n"
            f"fingerprint: {pem_fingerprint}\n"
            "roles: [publisher]\n"
        ).encode(),
    }
    return files


def scan_local_plugins(root: Path) -> list[LocalPluginEntry]:
    """扫描项目根下插件目录（含 plugins/ 与 plugins_installed/ 一层）。

    状态判定（经 verify_plugin_trust 真实验签）：
      signed_by_me       : 创作者签名有效且作者在本地信任列表（plugins/ 下）
      signed_trusted     : 维护者签名（信任根）或创作者已在信任列表
      signed_untrusted   : 创作者签名有效但作者未在信任列表
      unsigned           : 无有效签名
    """
    from .trust import TrustedUserList, TrustLevel, verify_plugin_trust

    entries: list[LocalPluginEntry] = []
    trusted = TrustedUserList()
    for plugins_dir in ("plugins", "plugins_installed"):
        base = root / plugins_dir
        if not base.is_dir():
            continue
        for plugin_dir in sorted(base.iterdir()):
            plugin_file = plugin_dir / "plugin.py"
            if not plugin_dir.is_dir() or not plugin_file.is_file():
                continue
            metadata = _read_metadata(plugin_file)
            decision = verify_plugin_trust(plugin_dir, "", trusted)
            status = "unsigned"
            author, fingerprint = "", ""
            if decision.level == TrustLevel.MaintainerSigned:
                status = "signed_trusted"
            elif decision.level == TrustLevel.CreatorTrusted and decision.creator is not None:
                status = "signed_by_me" if plugins_dir == "plugins" else "signed_trusted"
                author = decision.creator.username
                fingerprint = decision.creator.key_fingerprint
            elif decision.level == TrustLevel.CreatorUntrusted and decision.creator is not None:
                status = "signed_untrusted"
                author = decision.creator.username
                fingerprint = decision.creator.key_fingerprint
            entries.append(
                LocalPluginEntry(
                    path=plugin_dir,
                    name=str(metadata.get("name") or plugin_dir.name),
                    version=str(metadata.get("version") or "0.0.0"),
                    description=str(metadata.get("description") or ""),
                    status=status,
                    author_username=author,
                    fingerprint=fingerprint,
                )
            )
    return entries
