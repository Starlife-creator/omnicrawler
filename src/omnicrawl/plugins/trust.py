"""Three-tier trust model for the plugin ecosystem (aligned with Helios §13.6).

Trust levels:

- ``MaintainerSigned``   : ``maintainer.sig`` (or legacy ``plugin.py.sig``)
  verifies against the bundled trust root → automatically trusted, load.
- ``CreatorTrusted``     : ``creator.sig`` verifies with ``creator.identity``
  and the creator's fingerprint is in the local trust list → load.
- ``CreatorUntrusted``   : ``creator.sig`` verifies but the creator is not in
  the trust list → prompt the user (CLI prints how to trust; GUI dialog in a
  later stage); load only after explicit trust.
- ``Unsigned``           : no valid signature → rejected by policy; the loader
  keeps a documented developer-mode escape hatch for local plugins.

The trust list is a purely local decision (``trusted_users.json``), never
synced to any server. Trusting a creator trusts all plugins from that
fingerprint until revoked.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from . import signing
from .identity import CreatorIdentity

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


@dataclass(frozen=True, slots=True)
class TrustedUser:
    username: str
    key_fingerprint: str
    trusted_at: str
    source: str  # "p2p" | "manual"


class TrustedUserList:
    """本地信任列表：纯本地决策，不同步到任何服务器。"""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else DEFAULT_TRUST_LIST
        self._users: dict[str, TrustedUser] = {}  # key: fingerprint
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            LOGGER.warning("信任列表解析失败，按空列表处理: %s（%s）", self.path, exc)
            return
        for item in data.get("users", []):
            fingerprint = str(item.get("key_fingerprint", ""))
            if not fingerprint:
                continue
            self._users[fingerprint] = TrustedUser(
                username=str(item.get("username", "?")),
                key_fingerprint=fingerprint,
                trusted_at=str(item.get("trusted_at", "")),
                source=str(item.get("source", "manual")),
            )

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "schema_version": 1,
            "users": [
                {
                    "username": user.username,
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

    def add(
        self,
        creator: CreatorIdentity,
        *,
        source: str = "manual",
        path_hint: str = "",
    ) -> bool:
        if self.contains(creator.key_fingerprint):
            return False
        self._users[creator.key_fingerprint] = TrustedUser(
            username=creator.username,
            key_fingerprint=creator.key_fingerprint,
            trusted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            source=source,
        )
        self._save()
        LOGGER.info("已信任创作者 %s（指纹 %s）%s", creator.username, creator.key_fingerprint, path_hint)
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
) -> TrustDecision:
    """评估插件目录的信任等级（fail-closed：任何签名验证失败都降级）。

    信任根未配置时跳过维护者层级，但仍评估创作者层级——创作者公钥在
    ``creator.identity`` 内自带，不依赖信任根，因此本地插件在无信任根
    环境下也能完成身份验证与作者展示。
    """
    plugin_path = plugin_dir / "plugin.py"
    if not plugin_path.is_file():
        return TrustDecision(TrustLevel.Unsigned, "缺少 plugin.py")

    plugin_bytes = plugin_path.read_bytes()

    # 层级 1：维护者签名（信任根验证）——信任根未配置/非法时跳过
    trust_root = None
    if trust_source:
        try:
            trust_root = signing.load_public_key(trust_source)
        except (ValueError, TypeError):
            trust_root = None
    if trust_root is not None:
        for sig_name in ("maintainer.sig", "plugin.py.sig"):  # plugin.py.sig 为遗留兼容
            if _verify_sig_file(plugin_bytes, plugin_dir / sig_name, trust_root):
                return TrustDecision(TrustLevel.MaintainerSigned, f"{sig_name} 通过信任根验签")

    # 层级 2：创作者签名 + 信任列表（不依赖信任根）
    creator = _load_creator_identity(plugin_dir)
    if creator is not None:
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PublicKey as _Public,
            )

            public_key = _Public.from_public_bytes(creator.public_key)
        except Exception:  # noqa: BLE001
            return TrustDecision(TrustLevel.Unsigned, "creator.identity 公钥非法")
        if _verify_sig_file(plugin_bytes, plugin_dir / "creator.sig", public_key):
            if trusted.contains(creator.key_fingerprint):
                return TrustDecision(TrustLevel.CreatorTrusted, "创作者签名有效且在信任列表", creator)
            return TrustDecision(
                TrustLevel.CreatorUntrusted,
                "创作者签名有效但未在信任列表",
                creator,
            )
        return TrustDecision(TrustLevel.Unsigned, "creator.sig 验证失败")

    if trust_root is None:
        return TrustDecision(TrustLevel.Unsigned, "无有效签名（未配置信任根，且无创作者身份）")
    return TrustDecision(TrustLevel.Unsigned, "无有效签名")


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
