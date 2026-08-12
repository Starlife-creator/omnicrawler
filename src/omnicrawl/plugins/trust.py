"""Three-tier trust model for the plugin ecosystem (aligned with Helios §13.6).

Trust levels:

- ``MaintainerSigned``   : ``plugin.py.sig`` verifies against the bundled trust
  root → automatically trusted, load. (旧版 ``maintainer.sig`` 文件名已弃用，
  不再产生；新签名统一用 ``plugin.py.sig``。)
- ``CreatorTrusted``     : ``creator.sig`` verifies with ``creator.identity``
  and the creator's fingerprint is in the local trust list → load.
- ``CreatorUntrusted``   : ``creator.sig`` verifies but the creator is not in
  the trust list → prompt the user (CLI 打印信任指引；GUI 通过信任确认弹窗
  QMessageBox 询问，确认后写入 ``trusted_users.json``)；显式信任后才加载。
- ``Unsigned``           : no valid signature → rejected by policy; the loader
  keeps a documented developer-mode escape hatch for local plugins.

The trust list is a purely local decision (``trusted_users.json``), never
synced to any server. Trusting a creator trusts all plugins from that
fingerprint until revoked.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from . import signing
from .identity import (
    FINGERPRINT_ALGORITHM,
    CreatorIdentity,
    FingerprintMismatchError,
    IdentityError,
    derive_fingerprint,
)

LOGGER = logging.getLogger(__name__)

DEFAULT_TRUST_LIST = Path.home() / ".omnicrawl" / "trusted_users.json"


class TrustLevel(Enum):
    MaintainerSigned = "maintainer_signed"
    CreatorTrusted = "creator_trusted"
    CreatorUntrusted = "creator_untrusted"
    Unsigned = "unsigned"


@dataclass(frozen=True, slots=True)
class TrustDecision:
    level: TrustLevel
    reason: str
    creator: CreatorIdentity | None = None
    #: 通过验签的**那一份字节**。加载器必须执行这份内容，不得重新读盘，
    #: 否则验签与执行之间存在 TOCTOU 窗口（审查报告 S49）。
    verified_bytes: bytes | None = None
    #: 信任根是否真正加载成功。为 False 时 MaintainerSigned 层级**根本没被评估**，
    #: 调用方若要求市场级签名，必须据此拒绝而不是默认放行（审查报告 S51）。
    trust_root_available: bool = False


TRUST_LIST_SCHEMA_VERSION = 2


@dataclass(frozen=True, slots=True)
class TrustedUser:
    username: str
    public_key: bytes
    trusted_at: str
    source: str  # "p2p" | "manual" | "local"

    @property
    def key_fingerprint(self) -> str:
        """指纹永远由公钥现场推导，列表里不存在"可被改写的指纹字段"。"""
        return derive_fingerprint(self.public_key)


class TrustedUserList:
    """本地信任列表：纯本地决策，不同步到任何服务器。

    **存储的是公钥，不是指纹。** 指纹只是公钥的展示形式，随取随算。
    这样即使有人手工编辑 ``trusted_users.json``，也无法造出一条
    "指纹 A、公钥 B" 的错位记录——载入时会当场发现并丢弃。
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else DEFAULT_TRUST_LIST
        self._users: dict[str, TrustedUser] = {}  # key: 由公钥推导的指纹
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            LOGGER.warning("信任列表解析失败，按空列表处理: %s（%s）", self.path, exc)
            return
        version = int(data.get("schema_version", 1) or 1)
        for item in data.get("users", []):
            encoded = str(item.get("public_key", ""))
            declared = str(item.get("key_fingerprint", "")).strip().lower()
            if not encoded:
                # schema v1 只存指纹字符串，无公钥可校验 —— 无法证明这条记录
                # 对应哪把密钥，一律丢弃（硬断，不做兼容）。
                LOGGER.error(
                    "信任条目缺少 public_key，已丢弃（用户 %s / 指纹 %s）。"
                    "这是 v%d→v%d 的破坏性变更：请重新信任该创作者，"
                    "新记录会绑定公钥本身而非一串可伪造的指纹。",
                    item.get("username", "?"),
                    declared or "?",
                    version,
                    TRUST_LIST_SCHEMA_VERSION,
                )
                continue
            try:
                public_key = base64.b64decode(encoded, validate=True)
                user = TrustedUser(
                    username=str(item.get("username", "?")),
                    public_key=public_key,
                    trusted_at=str(item.get("trusted_at", "")),
                    source=str(item.get("source", "manual")),
                )
            except (ValueError, TypeError, IdentityError) as exc:
                LOGGER.error("信任条目公钥非法，已丢弃（%s）：%s", item.get("username", "?"), exc)
                continue
            if declared and declared != user.key_fingerprint:
                LOGGER.error(
                    "信任条目指纹与公钥不符，已丢弃：声明 %s，公钥实际 %s（文件疑似被篡改）",
                    declared,
                    user.key_fingerprint,
                )
                continue
            self._users[user.key_fingerprint] = user

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "schema_version": TRUST_LIST_SCHEMA_VERSION,
            "fingerprint_algorithm": FINGERPRINT_ALGORITHM,
            "users": [
                {
                    "username": user.username,
                    "public_key": base64.b64encode(user.public_key).decode("ascii"),
                    # 冗余写出便于人工核对；载入时以公钥为准，本字段仅做一致性校验
                    "key_fingerprint": user.key_fingerprint,
                    "trusted_at": user.trusted_at,
                    "source": user.source,
                }
                for user in sorted(self._users.values(), key=lambda u: u.key_fingerprint)
            ],
        }
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def contains(self, fingerprint: str) -> bool:
        return fingerprint in self._users

    def contains_key(self, public_key: bytes) -> bool:
        """按公钥判定信任（推荐入口）：调用方连指纹字符串都不必经手。"""
        try:
            return derive_fingerprint(public_key) in self._users
        except IdentityError:
            return False

    def add(
        self,
        creator: CreatorIdentity,
        *,
        source: str = "manual",
        path_hint: str = "",
    ) -> bool:
        # creator.key_fingerprint 是公钥的纯函数，构造 CreatorIdentity 时已校验公钥合法
        fingerprint = creator.key_fingerprint
        if self.contains(fingerprint):
            return False
        self._users[fingerprint] = TrustedUser(
            username=creator.username,
            public_key=creator.public_key,
            trusted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            source=source,
        )
        self._save()
        LOGGER.info("已信任创作者 %s（指纹 %s）%s", creator.username, fingerprint, path_hint)
        return True

    def revoke(self, fingerprint: str) -> bool:
        removed = self._users.pop(fingerprint, None)
        if removed is not None:
            self._save()
        return removed is not None

    def list_users(self) -> list[TrustedUser]:
        return sorted(self._users.values(), key=lambda u: u.trusted_at)


def _load_creator_identity(plugin_dir: Path) -> CreatorIdentity | None:
    path = plugin_dir / "creator.identity"
    if not path.is_file():
        return None
    try:
        return CreatorIdentity.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except FingerprintMismatchError as exc:
        # 指纹与公钥对不上不是"格式坏了"，是有人在冒充别人 —— 按安全事件记录
        LOGGER.error("拒绝加载：creator.identity 身份伪造嫌疑 %s（%s）", path, exc)
        return None
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        LOGGER.warning("creator.identity 解析失败: %s（%s）", path, exc)
        return None


def _verify_sig_file(plugin_bytes: bytes, sig_path: Path, public_key: Any) -> bool:
    if not sig_path.is_file():
        return False
    try:
        public_key.verify(sig_path.read_bytes(), plugin_bytes)
    except Exception:  # noqa: BLE001 - 验签失败即视为不可信
        return False
    return True


def verify_plugin_trust(
    plugin_dir: Path,
    trust_source: str,
    trusted: TrustedUserList,
    *,
    plugin_bytes: bytes | None = None,
) -> TrustDecision:
    """评估插件目录的信任等级（fail-closed：任何签名验证失败都降级）。

    ``plugin_bytes``：调用方已读好的 plugin.py 内容。传入后**不再读盘**，
    验签基于这份字节并把同一份字节放进 ``verified_bytes`` 返回——加载器
    据此执行，验签与执行之间的 TOCTOU 窗口即被关闭（审查报告 S49）。

    信任根未配置时跳过维护者层级，但仍评估创作者层级——创作者公钥在
    ``creator.identity`` 内自带，不依赖信任根，因此本地插件在无信任根
    环境下也能完成身份验证与作者展示。
    """
    plugin_path = plugin_dir / "plugin.py"
    if not plugin_path.is_file():
        return TrustDecision(TrustLevel.Unsigned, "缺少 plugin.py")

    if plugin_bytes is None:
        plugin_bytes = plugin_path.read_bytes()

    # 层级 1：维护者签名（信任根验证）
    trust_root = None
    if trust_source:
        try:
            trust_root = signing.load_public_key(trust_source)
        except (ValueError, TypeError, OSError) as exc:
            # 静默降级会让 MaintainerSigned 层级凭空消失，且调用方无从察觉（S51）。
            # 这里必须留下可诊断痕迹，并通过 trust_root_available 向上传递事实。
            LOGGER.error(
                "信任根加载失败，维护者签名层级未被评估: %s（%s）。"
                "要求市场级签名的调用方应据此拒绝加载。",
                trust_source,
                exc,
            )
            trust_root = None
    root_ok = trust_root is not None
    if trust_root is not None:
        sig_path = plugin_dir / "plugin.py.sig"
        if _verify_sig_file(plugin_bytes, sig_path, trust_root):
            return TrustDecision(
                TrustLevel.MaintainerSigned,
                "plugin.py.sig 通过信任根验签",
                verified_bytes=plugin_bytes,
                trust_root_available=root_ok,
            )

    # 层级 2：创作者签名 + 信任列表（不依赖信任根）
    creator = _load_creator_identity(plugin_dir)
    if creator is not None:
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PublicKey as _Public,
            )

            public_key = _Public.from_public_bytes(creator.public_key)
        except Exception:  # noqa: BLE001
            return TrustDecision(
                TrustLevel.Unsigned, "creator.identity 公钥非法", trust_root_available=root_ok
            )
        if _verify_sig_file(plugin_bytes, plugin_dir / "creator.sig", public_key):
            # 关键：用来验签的公钥与用来查信任列表的公钥是**同一个对象**，
            # 指纹由它现场推导。包里自称的指纹在 from_dict 阶段已被比对并丢弃，
            # 攻击者无法再用「自己的私钥 + 别人的指纹」冒名过关（B1）。
            if trusted.contains_key(creator.public_key):
                return TrustDecision(
                    TrustLevel.CreatorTrusted,
                    "创作者签名有效且公钥在信任列表",
                    creator,
                    verified_bytes=plugin_bytes,
                    trust_root_available=root_ok,
                )
            return TrustDecision(
                TrustLevel.CreatorUntrusted,
                "创作者签名有效但未在信任列表",
                creator,
                verified_bytes=plugin_bytes,
                trust_root_available=root_ok,
            )
        return TrustDecision(
            TrustLevel.Unsigned, "creator.sig 验证失败", trust_root_available=root_ok
        )

    if trust_root is None:
        return TrustDecision(
            TrustLevel.Unsigned,
            "无有效签名（未配置信任根，且无创作者身份）",
            trust_root_available=False,
        )
    return TrustDecision(TrustLevel.Unsigned, "无有效签名", trust_root_available=True)


def load_decision(decision: TrustDecision) -> tuple[bool, str]:
    """把信任评估映射为加载决策。"""
    if decision.level in (TrustLevel.MaintainerSigned, TrustLevel.CreatorTrusted):
        return True, decision.reason
    if decision.level == TrustLevel.CreatorUntrusted:
        assert decision.creator is not None
        return False, (
            f"插件作者：{decision.creator.username}，公钥指纹：{decision.creator.key_fingerprint}。"
            "该插件未经市场审核，可通过信任命令授权后加载。"
        )
    return False, decision.reason
