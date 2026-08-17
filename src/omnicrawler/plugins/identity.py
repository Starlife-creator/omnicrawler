"""Local user identity for the plugin ecosystem (creator signing).

Each user creates a local identity on first use: username + password (purely
local, never registered online) and an automatically generated ed25519 keypair.
The private key is encrypted with a password-derived key and stored in the OS
keyring-backed SecretsStore — it never touches the plugin directory, the repo,
or any build output (cold-key principle, aligned with Helios §13.7/§13.10).

The public key and fingerprint are safe to embed in plugin manifests
(``creator.identity``) and to distribute, because a public key has no secrecy
requirement and cannot be used to forge signatures.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# cryptography is an optional dependency (pyproject.toml → [security]).
_AESGCM: Any = None
_PBKDF2HMAC: Any = None
_HASHES: Any = None
_Ed25519PrivateKey: Any = None
_Ed25519PublicKey: Any = None


def _ensure_crypto() -> None:
    global _AESGCM, _PBKDF2HMAC, _HASHES, _Ed25519PrivateKey, _Ed25519PublicKey  # noqa: PLW0603
    if _AESGCM is not None:
        return
    try:
        from cryptography.hazmat.primitives import hashes as pyhashes
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey as _Private,
        )
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey as _Public,
        )
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _A
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC as _P
    except ModuleNotFoundError as exc:
        raise ImportError(
            "需要安装 cryptography 才能使用插件身份系统。请运行: pip install 'omnicrawler-platform[security]'"
        ) from exc
    _AESGCM = _A
    _PBKDF2HMAC = _P
    _HASHES = pyhashes
    _Ed25519PrivateKey = _Private
    _Ed25519PublicKey = _Public


PBKDF2_ITERATIONS = 600_000
_IDENTITY_KEY_PREFIX = "identity:"
USERNAME_RE = "^[a-z0-9_-]{2,32}$"


class IdentityError(ValueError):
    """Raised for identity creation/loading failures (fail-closed)."""


def _derive_key(password: str, salt: bytes) -> bytes:
    _ensure_crypto()
    return _PBKDF2HMAC(
        algorithm=_HASHES.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    ).derive(password.encode("utf-8"))


ED25519_PUBLIC_KEY_SIZE = 32
FINGERPRINT_ALGORITHM = "sha256-raw32-16"


class FingerprintMismatchError(IdentityError):
    """自称指纹与公钥实际推导值不符——视为身份伪造，fail-closed 拒绝。"""


def derive_fingerprint(public_key_bytes: bytes) -> str:
    """公钥指纹 = SHA-256(ed25519 公钥原始 32 字节) 前 16 字节 hex。

    **全生态唯一算法。** 输入是密钥的规范字节表示：无文本编码、无行尾、
    无 base64 折行差异，跨平台跨语言可复现。

    历史上并存过第二条「PEM 文本指纹」轨（``SHA-256(PEM 字节, CRLF 归一化)``），
    已于本次统一中废弃。废弃理由有二：其一，需要靠行尾归一化才能稳定的哈希
    输入本身就是设计缺陷；其二，双轨造成两套**互不认证**的信任命名空间——
    市场 CI 只校验 PEM 轨，运行时只信客户端轨，两边永不互查。
    """
    if len(public_key_bytes) != ED25519_PUBLIC_KEY_SIZE:
        raise IdentityError(
            f"ed25519 公钥必须是 {ED25519_PUBLIC_KEY_SIZE} 个原始字节，"
            f"实际 {len(public_key_bytes)} 字节；指纹不可由空公钥或非法公钥推导"
        )
    return hashlib.sha256(public_key_bytes).hexdigest()[:32]


# 内部旧调用别名（保持单一实现，避免再次分叉出第二条指纹轨）
_fingerprint = derive_fingerprint


def public_key_bytes_from_pem(value: str) -> bytes:
    """从 PEM 文件路径（或内联 PEM 文本）加载 ed25519 公钥，返回 32 原始字节。

    信任创作者从此必须绑定**公钥本身**（CLI ``trust add --pubkey`` 与 GUI
    信任表单共用此入口）——指纹是公钥的展示形式，反推不回密钥；只收指纹
    就等于收一串可伪造的字符串（审查报告 B1/N23①/N26）。
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    candidate = value.strip()
    maybe = Path(candidate)
    if maybe.is_file():
        candidate = maybe.read_text(encoding="utf-8")
    key = serialization.load_pem_public_key(candidate.encode("utf-8"))
    if not isinstance(key, Ed25519PublicKey):
        raise IdentityError("信任公钥必须是 ed25519 公钥")
    return key.public_bytes_raw()


@dataclass(frozen=True, slots=True)
class CreatorIdentity:
    """公开身份信息，嵌入插件 manifest 的 creator.identity（可随插件分发）。

    **不存储指纹字段。** 指纹是公钥的纯函数，任何时候都现场推导——这样
    "包里自称的指纹" 这个攻击者可控输入在类型层面就不存在，无处可填。
    """

    username: str
    public_key: bytes

    def __post_init__(self) -> None:
        # 构造即校验：空公钥 / 长度非法一律拒绝，杜绝 public_key=b"" 的裸指纹信任
        derive_fingerprint(self.public_key)

    @property
    def key_fingerprint(self) -> str:
        return derive_fingerprint(self.public_key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "username": self.username,
            "public_key": base64.b64encode(self.public_key).decode("ascii"),
            # 冗余写出仅供人工查看/诊断；读取端一律重新推导，绝不采信本字段
            "key_fingerprint": self.key_fingerprint,
            "fingerprint_algorithm": FINGERPRINT_ALGORITHM,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CreatorIdentity:
        """从 creator.identity 还原身份；自称指纹只用于**比对**，绝不用于信任判定。"""
        try:
            public_key = base64.b64decode(str(data["public_key"]), validate=True)
        except (KeyError, ValueError, TypeError) as exc:
            raise IdentityError("creator.identity 的 public_key 缺失或不是合法 base64") from exc
        identity = cls(username=str(data["username"]), public_key=public_key)
        declared = str(data.get("key_fingerprint", "")).strip().lower()
        if declared and declared != identity.key_fingerprint:
            raise FingerprintMismatchError(
                f"creator.identity 指纹与公钥不符：声明 {declared}，"
                f"公钥实际推导 {identity.key_fingerprint}（疑似身份冒充，已拒绝）"
            )
        return identity


@dataclass(slots=True)
class UserIdentity:
    """本地用户身份：私钥只存在于内存与加密存储中，绝不落盘明文。"""

    username: str
    signing_key: Any  # cryptography Ed25519PrivateKey
    created_at: datetime

    @property
    def public_key_bytes(self) -> bytes:
        return bytes(self.signing_key.public_key().public_bytes_raw())

    @property
    def key_fingerprint(self) -> str:
        """自身指纹同样现场推导，不缓存——保证与 CreatorIdentity 永远同源同算法。"""
        return derive_fingerprint(self.public_key_bytes)

    def sign_bytes(self, data: bytes) -> bytes:
        return self.signing_key.sign(data)

    def export_identity(self) -> CreatorIdentity:
        return CreatorIdentity(username=self.username, public_key=self.public_key_bytes)


def _payload_encode(identity: UserIdentity, password: str) -> str:
    _ensure_crypto()
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    key = _derive_key(password, salt)
    ciphertext = _AESGCM(key).encrypt(nonce, identity.signing_key.private_bytes_raw(), b"omnicrawler-identity")
    return json.dumps(
        {
            "v": 1,
            "created_at": identity.created_at.isoformat(),
            "salt": base64.b64encode(salt).decode("ascii"),
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "cipher": base64.b64encode(ciphertext).decode("ascii"),
        }
    )


def _payload_decode(payload: str, username: str, password: str) -> UserIdentity:
    _ensure_crypto()
    try:
        data = json.loads(payload)
        salt = base64.b64decode(str(data["salt"]))
        nonce = base64.b64decode(str(data["nonce"]))
        ciphertext = base64.b64decode(str(data["cipher"]))
        created_at = datetime.fromisoformat(str(data["created_at"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise IdentityError("身份存储格式损坏") from exc
    key = _derive_key(password, salt)
    try:
        private_bytes = _AESGCM(key).decrypt(nonce, ciphertext, b"omnicrawler-identity")
    except Exception as exc:
        raise IdentityError("身份解密失败（密码错误或存储已损坏）") from exc
    signing_key = _Ed25519PrivateKey.from_private_bytes(private_bytes)
    return UserIdentity(username=username, signing_key=signing_key, created_at=created_at)


class IdentityStore:
    """身份存取：私钥经密码二次加密后存入 OS keyring 保护的 SecretsStore。"""

    def __init__(self, store: Any | None = None, store_path: str | Path | None = None) -> None:
        if store is None:
            from ..core.secrets_store import SecretsStore

            store = SecretsStore(store_path)
        self.store = store

    def exists(self, username: str) -> bool:
        return self.store.get(_IDENTITY_KEY_PREFIX + username) is not None

    def create(self, username: str, password: str) -> UserIdentity:
        if not password:
            raise IdentityError("密码不能为空")
        # B01-014：USERNAME_RE 从"定义了却不生效"变为创建路径强制；username 参与文件/密钥构造。
        if not re.fullmatch(USERNAME_RE, username):
            raise IdentityError(
                f"用户名非法（仅允许小写字母/数字/_-，长度 2-32）: {username!r}"
            )
        if self.exists(username):
            raise IdentityError(f"用户名 {username} 已存在（同一台机器用户名唯一）")
        _ensure_crypto()
        signing_key = _Ed25519PrivateKey.generate()
        identity = UserIdentity(
            username=username,
            signing_key=signing_key,
            created_at=datetime.now(timezone.utc),
        )
        self.store.set(_IDENTITY_KEY_PREFIX + username, _payload_encode(identity, password))
        return identity

    def load(self, username: str, password: str) -> UserIdentity:
        payload = self.store.get(_IDENTITY_KEY_PREFIX + username)
        if payload is None:
            raise IdentityError(f"身份不存在: {username}")
        return _payload_decode(payload, username, password)

    def delete(self, username: str, password: str) -> bool:
        self.load(username, password)  # 密码错误时拒绝删除（fail-closed）
        self.store.delete(_IDENTITY_KEY_PREFIX + username)
        return True

    def list_usernames(self) -> list[str]:
        prefix = _IDENTITY_KEY_PREFIX
        return sorted(key[len(prefix) :] for key in self.store.keys() if key.startswith(prefix))
