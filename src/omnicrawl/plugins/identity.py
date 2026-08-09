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
            "需要安装 cryptography 才能使用插件身份系统。请运行: pip install 'omnicrawl-platform[security]'"
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


def _fingerprint(public_key_bytes: bytes) -> str:
    """公钥指纹：SHA-256(公钥原始字节) 前 16 字节 hex（生态绝对唯一标识）。"""
    return hashlib.sha256(public_key_bytes).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class CreatorIdentity:
    """公开身份信息，嵌入插件 manifest 的 creator.identity（可随插件分发）。"""

    username: str
    public_key: bytes
    key_fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "username": self.username,
            "public_key": base64.b64encode(self.public_key).decode("ascii"),
            "key_fingerprint": self.key_fingerprint,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CreatorIdentity:
        return cls(
            username=str(data["username"]),
            public_key=base64.b64decode(str(data["public_key"])),
            key_fingerprint=str(data["key_fingerprint"]),
        )


@dataclass(slots=True)
class UserIdentity:
    """本地用户身份：私钥只存在于内存与加密存储中，绝不落盘明文。"""

    username: str
    signing_key: Any  # cryptography Ed25519PrivateKey
    key_fingerprint: str
    created_at: datetime

    def sign_bytes(self, data: bytes) -> bytes:
        return self.signing_key.sign(data)

    def export_identity(self) -> CreatorIdentity:
        return CreatorIdentity(
            username=self.username,
            public_key=self.signing_key.public_key().public_bytes_raw(),
            key_fingerprint=self.key_fingerprint,
        )


def _payload_encode(identity: UserIdentity, password: str) -> str:
    _ensure_crypto()
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    key = _derive_key(password, salt)
    ciphertext = _AESGCM(key).encrypt(nonce, identity.signing_key.private_bytes_raw(), b"omnicrawl-identity")
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
        private_bytes = _AESGCM(key).decrypt(nonce, ciphertext, b"omnicrawl-identity")
    except Exception as exc:
        raise IdentityError("身份解密失败（密码错误或存储已损坏）") from exc
    signing_key = _Ed25519PrivateKey.from_private_bytes(private_bytes)
    fingerprint = _fingerprint(signing_key.public_key().public_bytes_raw())
    return UserIdentity(
        username=username,
        signing_key=signing_key,
        key_fingerprint=fingerprint,
        created_at=created_at,
    )


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
        if self.exists(username):
            raise IdentityError(f"用户名 {username} 已存在（同一台机器用户名唯一）")
        _ensure_crypto()
        signing_key = _Ed25519PrivateKey.generate()
        fingerprint = _fingerprint(signing_key.public_key().public_bytes_raw())
        identity = UserIdentity(
            username=username,
            signing_key=signing_key,
            key_fingerprint=fingerprint,
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
