"""插件本地签名、上传包生成与本地目录扫描（GUI 生态闭环核心）。

- ``sign_plugin_local``   ：本地一键签名（creator-sign + 自动加入本地信任列表），
  等价于 ``tools/sign_plugin.py local-sign``，供 GUI 直接调用；
- ``build_plugin_upload`` ：生成提交市场仓库的 PR 文件集（plugin.py /
  creator.sig / creator.identity / plugin.yaml / listing.md /
  keys/<raw32指纹>.pub.pem / authors/<username>.yaml）；
- ``build_template_upload``：模板同构（template.yaml 注入 template: 元数据块）；
- ``scan_local_plugins``  ：扫描 plugins/ 目录，把条目分为
  已签名且属于当前用户 / 已签名未信任 / 已签名已信任 / 未签名 四态。

指纹约定（2026-08 统一后）：
- **全生态只有一条指纹轨**：``SHA-256(ed25519 公钥原始 32 字节) 前 16 字节 hex``
  （identity.derive_fingerprint）。
- 历史上的第二条「PEM 文本指纹」轨（``SHA-256(PEM 字节, CRLF 归一化)``）已废弃：
  双轨 = 两套互不认证的信任命名空间，且 PEM 文本需要行尾归一化才稳定，本身是设计缺陷。
- 上传包里的 ``author_fingerprint``、``keys/*.pub.pem`` 文件名、``authors/*.yaml``
  的 fingerprint 统一使用这条 raw32 轨。
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .identity import IdentityStore, UserIdentity
from .package_manifest import (
    CREATOR_SIGNATURE_NAME,
    MANIFEST_NAME,
    PackageType,
    sign_creator_package,
    verify_package,
)
from .trust import TrustedUserList

_PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_TEMPLATE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]*(?:/[a-z0-9_-]+)*$")


class PackagingError(ValueError):
    """插件打包/签名流程错误（fail-closed，消息面向用户）。"""


def _require_license(metadata: dict[str, Any], kind: str) -> str:
    """门 2（Phase 1，方案 A1）：license 必填，删除隐式默认。

    插件：SPDX 白名单内标识（市场 CI 门 2 校验，此处只查非空）；
    模板：数据/服务条款自由文本（必填即可）。未声明 → PackagingError，
    迫使作者显式选择，而非静默落为 MIT。
    """
    value = str(metadata.get("license") or "").strip()
    if not value:
        raise PackagingError(
            f"{kind}元数据缺少 license 声明（Phase 1 起必填，无隐式默认）。"
            f"插件请从 SPDX 白名单选择（如 MIT / Apache-2.0）；"
            f"模板填数据/服务条款说明。"
        )
    return value


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
    #: 作者公钥原始字节（存在时可用）：信任操作必须绑定公钥本体，
    #: 而不是把 fingerprint 字符串当凭据（审查报告 N23①/N26）。
    public_key: bytes = b""
    plugin_types: tuple[str, ...] = ()
    category: str = ""
    tags: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    execution_mode: str = "subprocess"


def _load_user(username: str, password: str) -> UserIdentity:
    try:
        return IdentityStore().load(username, password)
    except Exception as exc:  # noqa: BLE001 - 身份库错误统一转可读异常
        raise PackagingError(f"身份加载失败：{exc}") from exc


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


def _metadata_strings(metadata: dict[str, Any], key: str) -> tuple[str, ...]:
    value = metadata.get(key, [])
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def sign_plugin_local(plugin_dir: Path, *, username: str, password: str, target: str = "plugin.py") -> str:
    """完成可分享包：整包 manifest 签名 + 旧 creator.sig 兼容轨。

    返回客户端身份指纹（显示用）。
    """
    plugin_dir = plugin_dir.resolve()
    target_path = plugin_dir / target
    if not target_path.is_file():
        raise PackagingError(f"缺少待签名文件 {target}: {target_path}")
    user = _load_user(username, password)
    metadata = _read_metadata(target_path) if target == "plugin.py" else {}
    package_type: PackageType = "plugin" if target == "plugin.py" else "template"
    package_id = _plugin_id_from_dir(plugin_dir) if package_type == "plugin" else plugin_dir.name
    version = str(metadata.get("version") or "0.1.0")
    signed = sign_creator_package(
        plugin_dir,
        package_type=package_type,
        package_id=package_id,
        version=version,
        identity=user,
        legacy_target=target,
    )
    TrustedUserList().add(signed.creator, source="local", path_hint=f"（{plugin_dir}）")
    return signed.creator.key_fingerprint


def _submission_files(
    package_dir: Path,
    prefix: str,
    *,
    market_metadata: dict[str, str] | None = None,
) -> dict[str, bytes]:
    """Return the exact creator-signed folder as a market submission payload."""
    verification = verify_package(package_dir)
    manifest = json.loads((package_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    relative_files = set(str(name) for name in manifest["files"])
    relative_files.update({MANIFEST_NAME, CREATOR_SIGNATURE_NAME, "creator.sig"})
    result: dict[str, bytes] = {}
    for rel in sorted(relative_files):
        source = package_dir / Path(*rel.split("/"))
        if source.is_file():
            result[f"{prefix}/{rel}"] = source.read_bytes()
    submission = {
        "schema_version": 1,
        "status": "creator_signed",
        "requested_username": verification.creator.username,
        "creator_fingerprint": verification.creator.key_fingerprint,
        "package_manifest_sha256": verification.manifest_sha256,
    }
    if market_metadata:
        submission["market_metadata"] = market_metadata
    result[f"{prefix}/submission.json"] = (
        json.dumps(
            submission,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    return result


def build_plugin_submission(
    plugin_dir: Path,
    *,
    username: str,
    password: str,
    listing: str | None = None,
) -> dict[str, bytes]:
    """Finalize and return a distribution-neutral Draft-PR submission."""
    plugin_dir = plugin_dir.resolve()
    plugin_file = plugin_dir / "plugin.py"
    if not plugin_file.is_file():
        raise PackagingError(f"缺少 plugin.py: {plugin_file}")
    metadata = _read_metadata(plugin_file)
    plugin_id = _plugin_id_from_dir(plugin_dir)
    version = str(metadata.get("version") or "0.1.0")
    if listing is not None:
        (plugin_dir / "listing.md").write_text(listing, encoding="utf-8")
    if not (plugin_dir / "listing.md").is_file():
        raise PackagingError("缺少 listing.md：完成并签名前必须填写插件说明")
    user = _load_user(username, password)
    signed = sign_creator_package(
        plugin_dir,
        package_type="plugin",
        package_id=plugin_id,
        version=version,
        identity=user,
        legacy_target="plugin.py",
    )
    TrustedUserList().add(signed.creator, source="local", path_hint=f"（{plugin_dir}）")
    return _submission_files(
        plugin_dir,
        f"submissions/plugins/{signed.creator.key_fingerprint}/{plugin_id}",
    )


def build_template_submission(
    template_dir: Path,
    *,
    username: str,
    password: str,
    template_id: str,
    version: str,
    name: str = "",
    category: str = "",
    summary: str = "",
    listing: str | None = None,
) -> dict[str, bytes]:
    """Template counterpart of :func:`build_plugin_submission`."""
    template_dir = template_dir.resolve()
    if not (template_dir / "template.yaml").is_file():
        raise PackagingError(f"缺少 template.yaml: {template_dir / 'template.yaml'}")
    if not _TEMPLATE_ID_RE.match(template_id) or ".." in template_id:
        raise PackagingError(f"非法模板 ID: {template_id}")
    if listing is not None:
        (template_dir / "listing.md").write_text(listing, encoding="utf-8")
    if not (template_dir / "listing.md").is_file():
        raise PackagingError("缺少 listing.md：完成并签名前必须填写模板说明")
    user = _load_user(username, password)
    signed = sign_creator_package(
        template_dir,
        package_type="template",
        package_id=template_id,
        version=version,
        identity=user,
        legacy_target="template.yaml",
    )
    TrustedUserList().add(signed.creator, source="local", path_hint=f"（{template_dir}）")
    return _submission_files(
        template_dir,
        f"submissions/templates/{signed.creator.key_fingerprint}/{template_id}",
        market_metadata={
            "name": name.strip(),
            "category": category.strip(),
            "summary": summary.strip(),
        },
    )


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
    # category 是自由的市场业务分类；plugin_types 才是宿主受控的运行扩展点。
    # 未声明 category 的旧插件继续回退到首个 plugin_type，保持上传兼容。
    category = str(metadata.get("category") or (metadata.get("plugin_types") or ["source"])[0])
    summary = str(metadata.get("description") or "")
    if not summary:
        raise PackagingError("插件缺少介绍：请在 PLUGIN_METADATA 中填写 description")

    pem = _public_pem(user)
    # raw32 指纹：与 keys/*.pub.pem 文件名、authors/*.yaml、creator.identity 全生态一致
    author_fingerprint = creator.key_fingerprint

    manifest = {
        "id": plugin_id,
        "name": name,
        "version": version,
        "publisher": username,
        "author_fingerprint": author_fingerprint,
        "category": category,
        "summary": summary,
        "description_file": f"plugins/{plugin_id}/listing.md",
        "plugin_file": f"plugins/{plugin_id}/plugin.py",
        # 本 PR 实际携带的创作者签名轨（市场 CI 据此验签并核对作者指纹）
        "creator_signature_file": f"plugins/{plugin_id}/creator.sig",
        "creator_identity_file": f"plugins/{plugin_id}/creator.identity",
        # 维护者计数器签名：由维护者审核后补签（plugin.py.sig），**上传包不伪造该文件**。
        # 修复前 manifest 声明它却不出产它，导致 GUI 一键上传的 PR 必然过不了自家 CI
        # （审查报告 S50）。
        "signature_file": f"plugins/{plugin_id}/plugin.py.sig",
        "signature_algorithm": "ed25519",
        "plugin_types": list(metadata.get("plugin_types") or ["source"]),
        "permissions": list(metadata.get("permissions") or []),
        "execution_mode": str(metadata.get("execution_mode") or "subprocess"),
        "domains": list(metadata.get("domains") or []),
        "input_files": list(metadata.get("input_files") or []),
        "dependencies": list(metadata.get("dependencies") or []),
        "required_capabilities": dict(metadata.get("required_capabilities") or {}),
        "state_schema_version": int(metadata.get("state_schema_version") or 1),
        "compatible_core": f">={metadata.get('min_core_version') or '0.7.0'}",
        # 门 2（Phase 1，方案 A1）：license 必填——删除隐式 MIT 回退，
        # 未声明即打包失败（fail-closed），迫使作者显式选择许可。
        "license": _require_license(metadata, "插件"),
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
            "# 说明：signature_file（plugin.py.sig）为维护者审核后计数器签名，\n"
            "# 提交 PR 阶段该文件尚未生成，属正常状态；审核通过补签后 CI 即绿。\n"
            + yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False)
        ).encode("utf-8"),
        f"plugins/{plugin_id}/listing.md": listing.encode("utf-8"),
        f"keys/{author_fingerprint}.pub.pem": pem,
        f"authors/{username}.yaml": (
            "# 作者记录（首次上传自动生成；指纹 = SHA-256(ed25519 公钥原始字节) 前 16 字节 hex）\n"
            f"username: {username}\n"
            f"display_name: {username}\n"
            f"pubkey_ref: ../keys/{author_fingerprint}.pub.pem\n"
            f"fingerprint: {author_fingerprint}\n"
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
    tpl = raw.get("template")
    source_block = tpl if isinstance(tpl, dict) else {}
    raw["template"] = {
        "id": template_id,
        "name": name,
        "version": version,
        "category": category,
        "description": summary,
        "publisher": username,
        "author_fingerprint": "0" * 32,  # 占位：下方用创作者真实指纹覆盖
        # 门 2（Phase 1，方案 A1）：保留模板自带声明，缺失即报错（fail-closed，
        # 与 build_plugin_upload 对齐）——不再隐式回退 MIT
        "min_core_version": str(source_block.get("min_core_version") or "0.7.0"),
        "license": _require_license(source_block, "模板"),
    }

    user = _load_user(username, password)
    creator = user.export_identity()
    pem = _public_pem(user)
    author_fingerprint = creator.key_fingerprint
    raw["template"]["author_fingerprint"] = author_fingerprint
    content = yaml.safe_dump(raw, allow_unicode=True, sort_keys=False).encode("utf-8")

    files: dict[str, bytes] = {
        f"templates/{template_id}/template.yaml": content,
        # B01-013：模板分发签名轨对齐插件路径——上传包不伪造 template.yaml.sig。
        # 模板强制要求维护者冷密钥签名（市场仓 generate_catalog 用信任根验签），
        # 创作者热密钥签的这份 sig 必然被 CI 拒。这里只产创作者轨（creator.sig +
        # creator.identity），template.yaml.sig 由维护者审核后补签，缺失状态显式可见。
        f"templates/{template_id}/creator.sig": user.sign_bytes(content),
        f"templates/{template_id}/creator.identity": (
            json.dumps(creator.to_dict(), ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8"),
        f"templates/{template_id}/listing.md": listing.encode("utf-8"),
        f"keys/{author_fingerprint}.pub.pem": pem,
        f"authors/{username}.yaml": (
            f"username: {username}\n"
            f"display_name: {username}\n"
            f"pubkey_ref: ../keys/{author_fingerprint}.pub.pem\n"
            f"fingerprint: {author_fingerprint}\n"
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
    for plugins_dir in ("plugins", "plugins_local", "plugins_shared", "plugins_installed"):
        base = root / plugins_dir
        if not base.is_dir():
            continue
        candidates = sorted(path.parent for path in base.rglob("plugin.py"))
        for plugin_dir in candidates:
            plugin_file = plugin_dir / "plugin.py"
            metadata = _read_metadata(plugin_file)
            decision = verify_plugin_trust(plugin_dir, "", trusted)
            status = "unsigned"
            author, fingerprint, public_key = "", "", b""
            if decision.level == TrustLevel.MaintainerSigned:
                status = "signed_trusted"
            elif decision.level == TrustLevel.CreatorTrusted and decision.creator is not None:
                status = "signed_by_me" if plugins_dir == "plugins" else "signed_trusted"
                author = decision.creator.username
                fingerprint = decision.creator.key_fingerprint
                public_key = decision.creator.public_key
            elif decision.level == TrustLevel.CreatorUntrusted and decision.creator is not None:
                status = "signed_untrusted"
                author = decision.creator.username
                fingerprint = decision.creator.key_fingerprint
                public_key = decision.creator.public_key
            entries.append(
                LocalPluginEntry(
                    path=plugin_dir,
                    name=str(metadata.get("name") or plugin_dir.name),
                    version=str(metadata.get("version") or "0.0.0"),
                    description=str(metadata.get("description") or ""),
                    status=status,
                    author_username=author,
                    fingerprint=fingerprint,
                    public_key=public_key,
                    plugin_types=tuple(
                        item.casefold() for item in _metadata_strings(metadata, "plugin_types")
                    ),
                    category=str(metadata.get("category") or ""),
                    tags=_metadata_strings(metadata, "tags"),
                    permissions=_metadata_strings(metadata, "permissions"),
                    execution_mode=str(metadata.get("execution_mode") or "subprocess"),
                )
            )
    return entries
