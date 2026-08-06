"""secrets_store：凭据加密存储基础设施（S2.2.1）。

AES-GCM 加密整包落盘，主密钥优先 OS keyring（keyring 包）；keyring 无可用后端
时自动 fallback 到用户密码派生（PBKDF2-HMAC-SHA256，密码取环境变量
``OMNICRAWL_MASTER_PASSWORD``），不抛未捕获异常。写入采用"临时文件 + 原子
rename"，中途崩溃不残留损坏文件。

文件格式（``FILE_MAGIC + nonce(12) + AESGCM-密文``，明文为 JSON 映射）：
    {key: base64(密文)}  -- 键名明文、值密文，二次掩码对零。
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import types
from pathlib import Path
from typing import Protocol

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

try:
    import keyring as _keyring_module
except ImportError:  # pragma: no cover - 无 keyring 时依赖可选
    _keyring_module = None  # type: ignore[assignment]  # try 绑定为 Module 类型

_keyring: types.ModuleType | None = _keyring_module


class _KeyringBackend(Protocol):
    """keyring 可用后端的最小接口（keyring.set_keyring 注入的 mock 也满足）。"""

    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...

SERVICE = "omnicrawler"
ACCOUNT = "secrets-master"
ENV_PASSWORD = "OMNICRAWL_MASTER_PASSWORD"
FILE_MAGIC = b"OMNICRWL-SECRETS-1\n"
PBKDF2_ITERATIONS = 600_000
DERIVE_SALT = b"omnicrawler-secrets-v1"
KEY_LENGTH = 32


class SecretsStoreError(RuntimeError):
    """secrets 存储不可用（keyring 与密码派生均不可用、文件损坏等）。"""


def _derived_key(password: str, salt: bytes = DERIVE_SALT) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=KEY_LENGTH, salt=salt, iterations=PBKDF2_ITERATIONS
    )
    return kdf.derive(password.encode("utf-8"))


class SecretsStore:
    """加密凭据存储。``keyring_api`` 仅测试注入用，默认使用系统 keyring。"""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        keyring_api: _KeyringBackend | None = None,
    ) -> None:
        self.path = Path(path) if path else Path.home() / ".omnicrawl" / "secrets.bin"
        self.keyring = _keyring if keyring_api is None else keyring_api
        self._cache: dict[str, bytes] | None = None

    # -- 密钥获取 ----------------------------------------------------------

    def _master_key(self) -> bytes:
        """OS keyring 优先；失败时自动 fallback 密码派生，绝不抛未捕获异常。"""
        if self.keyring is not None:
            try:
                encoded = self.keyring.get_password(SERVICE, ACCOUNT)
                if encoded:
                    return base64.b64decode(str(encoded).encode("ascii"))
                raw = secrets.token_bytes(KEY_LENGTH)
                self.keyring.set_password(SERVICE, ACCOUNT, base64.b64encode(raw).decode("ascii"))
                return raw
            except Exception:
                pass  # 后端不可用/权限失败 → 走密码派生
        return self._password_derived_key()

    def _password_derived_key(self) -> bytes:
        password = os.environ.get(ENV_PASSWORD)
        if not password:
            raise SecretsStoreError(
                f"系统 keyring 不可用且未设置环境变量 {ENV_PASSWORD}，无法安全存取凭据"
            )
        return _derived_key(password)

    # -- 存储读写 ----------------------------------------------------------

    def _load(self) -> dict[str, bytes]:
        if self._cache is not None:
            return self._cache
        entries: dict[str, bytes] = {}
        if self.path.is_file():
            raw = self.path.read_bytes()
            if not raw.startswith(FILE_MAGIC):
                raise SecretsStoreError(f"secrets 文件格式损坏: {self.path}")
            key = self._master_key()
            nonce = raw[len(FILE_MAGIC) : len(FILE_MAGIC) + 12]
            ciphertext = raw[len(FILE_MAGIC) + 12 :]
            try:
                plaintext = AESGCM(key).decrypt(nonce, ciphertext, FILE_MAGIC)
            except Exception as exc:
                raise SecretsStoreError(f"secrets 文件解密失败（密钥不匹配?）: {self.path}") from exc
            stored = json.loads(plaintext.decode("utf-8"))
            entries = {str(k): base64.b64decode(v) for k, v in stored.items()}
        self._cache = entries
        return entries

    def _save(self) -> None:
        if self._cache is None:
            raise SecretsStoreError("内部状态错误: 缓存未加载，无法写盘")
        cache = self._cache
        key = self._master_key()
        plaintext = json.dumps(
            {k: base64.b64encode(v).decode("ascii") for k, v in cache.items()}
        ).encode("utf-8")
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(key).encrypt(nonce, plaintext, FILE_MAGIC)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_bytes(FILE_MAGIC + nonce + ciphertext)
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass  # Windows 无 POSIX 权限语义，靠 AES-GCM 兜底

    # -- API ---------------------------------------------------------------

    def get(self, key: str) -> str | None:
        value = self._load().get(key)
        return value.decode("utf-8") if value is not None else None

    def set(self, key: str, value: str) -> None:
        cache = self._load()
        cache[key] = value.encode("utf-8")
        self._save()

    def delete(self, key: str) -> bool:
        cache = self._load()
        if key not in cache:
            return False
        del cache[key]
        self._save()
        return True

    def keys(self) -> list[str]:
        return list(self._load())

    def contains_plaintext(self) -> bool:
        """当前文件是否为明文（调试/自检用）。"""
        if not self.path.is_file():
            return False
        raw = self.path.read_bytes()
        return FILE_MAGIC not in raw or len(raw) <= len(FILE_MAGIC) + 12

    # -- 单 blob 加解密（供 cookie 等独立文件加密，S2.2.3） --------------------

    def encrypt(self, data: bytes) -> bytes:
        """加密任意字节流，返回自包含 blob（magic+nonce+密文）。"""
        key = self._master_key()
        nonce = secrets.token_bytes(12)
        return FILE_MAGIC + nonce + AESGCM(key).encrypt(nonce, data, FILE_MAGIC)

    def decrypt(self, blob: bytes) -> bytes:
        """解密 ``encrypt`` 产生的 blob。"""
        if not blob.startswith(FILE_MAGIC):
            raise SecretsStoreError("secrets blob 格式损坏")
        key = self._master_key()
        nonce = blob[len(FILE_MAGIC) : len(FILE_MAGIC) + 12]
        ciphertext = blob[len(FILE_MAGIC) + 12 :]
        try:
            return AESGCM(key).decrypt(nonce, ciphertext, FILE_MAGIC)
        except Exception as exc:
            raise SecretsStoreError("secrets blob 解密失败（密钥不匹配?）") from exc
