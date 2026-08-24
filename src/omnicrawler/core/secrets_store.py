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
import threading
import types
from pathlib import Path
from typing import Any, Protocol

# cryptography is an optional dependency (pyproject.toml → [security]).
# Lazy-import to allow importing secrets_store without cryptography installed
# (e.g. e2e CI where only html/pdf/browser/dev extras are present).
_cryptography_imported = False
hashes: Any  # placeholder for type-checkers; real binding set by _ensure_crypto()
AESGCM: Any
PBKDF2HMAC: Any


def _ensure_crypto() -> None:
    """Import cryptography primitives on first use; raise a clear error if missing."""
    global hashes, AESGCM, PBKDF2HMAC, _cryptography_imported  # noqa: PLW0603
    if _cryptography_imported:
        return
    try:
        from cryptography.hazmat.primitives import hashes as _hashes
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _AESGCM
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC as _PBKDF2HMAC
    except ModuleNotFoundError as exc:
        raise SecretsStoreError(
            "需要安装 cryptography 才能使用凭据加密存储。"
            "请运行: pip install 'omnicrawler-platform[security]'"
        ) from exc
    hashes = _hashes
    AESGCM = _AESGCM
    PBKDF2HMAC = _PBKDF2HMAC
    _cryptography_imported = True

_UNSET = object()
_keyring: types.ModuleType | None | object = _UNSET


def _load_keyring() -> types.ModuleType | None:
    """按需加载 keyring（模块导入零开销；冷启动 GUI 不拉起 keyring 链≈197ms）。

    首次调用后缓存结果；keyring 未安装时返回 None（依赖可选）。
    """
    global _keyring
    if _keyring is _UNSET:
        try:
            import keyring as _keyring_module
        except ImportError:  # pragma: no cover - 无 keyring 时依赖可选
            _keyring = None
        else:
            _keyring = _keyring_module
    return _keyring  # type: ignore[return-value]


class _KeyringBackend(Protocol):
    """keyring 可用后端的最小接口（keyring.set_keyring 注入的 mock 也满足）。"""

    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...

SERVICE = "omnicrawler"
ACCOUNT = "secrets-master"
ENV_PASSWORD = "OMNICRAWL_MASTER_PASSWORD"
FILE_MAGIC = b"OMNICRWL-SECRETS-1\n"
# B05-001：v2 格式在文件头带随机盐（16 字节），旧 v1 无盐段用 DERIVE_SALT 派生。
FILE_MAGIC_V2 = b"OMNICRWL-SECRETS-2\n"
SALT_LENGTH = 16
PBKDF2_ITERATIONS = 600_000
DERIVE_SALT = b"omnicrawler-secrets-v1"
KEY_LENGTH = 32


class SecretsStoreError(RuntimeError):
    """secrets 存储不可用（keyring 与密码派生均不可用、文件损坏等）。"""


def _derived_key(password: str, salt: bytes = DERIVE_SALT) -> bytes:
    _ensure_crypto()
    kdf = PBKDF2HMAC(  # type: ignore[misc]
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
        env_path = os.environ.get("OMNICRAWL_SECRET_STORE_PATH", "")
        self.path = (
            Path(path)
            if path
            else Path(env_path)
            if env_path
            else Path.home() / ".omnicrawler" / "secrets.bin"
        )
        self.keyring = (
            None
            if os.environ.get("OMNICRAWL_KEYRING_DISABLE")
            else (_load_keyring() if keyring_api is None else keyring_api)
        )
        self._cache: dict[str, bytes] | None = None
        # B05-003：加载/保存/增删改统一串行化，防多线程并发写盘竞态
        self._io_lock = threading.RLock()

    # -- 密钥获取 ----------------------------------------------------------

    def _master_key(self, salt: bytes = DERIVE_SALT) -> bytes:
        """OS keyring 优先；失败时自动 fallback 密码派生，绝不抛未捕获异常。

        B05-001：密码派生使用文件头随机盐（新文件）或 DERIVE_SALT（旧 v1 兼容）。
        """
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
        return self._password_derived_key(salt)

    def _password_derived_key(self, salt: bytes) -> bytes:
        password = os.environ.get(ENV_PASSWORD)
        if not password:
            raise SecretsStoreError(
                f"系统 keyring 不可用且未设置环境变量 {ENV_PASSWORD}，无法安全存取凭据"
            )
        return _derived_key(password, salt=salt)

    # -- 存储读写 ----------------------------------------------------------

    def _load(self) -> dict[str, bytes]:
        with self._io_lock:
            if self._cache is not None:
                return self._cache
            _ensure_crypto()
            entries: dict[str, bytes] = {}
            if self.path.is_file():
                raw = self.path.read_bytes()
                if not (raw.startswith(FILE_MAGIC) or raw.startswith(FILE_MAGIC_V2)):
                    raise SecretsStoreError(f"secrets 文件格式损坏: {self.path}")
                key, nonce_offset = self._read_header(raw)
                nonce = raw[nonce_offset : nonce_offset + 12]
                ciphertext = raw[nonce_offset + 12 :]
                try:
                    plaintext = AESGCM(key).decrypt(nonce, ciphertext, FILE_MAGIC)
                except Exception as exc:
                    raise SecretsStoreError(f"secrets 文件解密失败（密钥不匹配?）: {self.path}") from exc
                stored = json.loads(plaintext.decode("utf-8"))
                entries = {str(k): base64.b64decode(v) for k, v in stored.items()}
            self._cache = entries
            return entries

    def _read_header(self, raw: bytes) -> tuple[bytes, int]:
        """解析文件头，返回 (master_key, nonce 起始偏移)。

        v2：MAGIC_V2 + salt(16) + nonce + ciphertext
        v1：MAGIC + nonce + ciphertext（无盐段，用 DERIVE_SALT 派生）
        """
        if raw.startswith(FILE_MAGIC_V2):
            salt = raw[len(FILE_MAGIC_V2) : len(FILE_MAGIC_V2) + SALT_LENGTH]
            return self._master_key(salt), len(FILE_MAGIC_V2) + SALT_LENGTH
        return self._master_key(DERIVE_SALT), len(FILE_MAGIC)

    def _save(self) -> None:
        with self._io_lock:
            if self._cache is None:
                raise SecretsStoreError("内部状态错误: 缓存未加载，无法写盘")
            _ensure_crypto()
            cache = self._cache
            # B05-001：每次写盘使用新的随机盐，派生密钥与文件绑定
            salt = secrets.token_bytes(SALT_LENGTH)
            key = self._master_key(salt)
            plaintext = json.dumps(
                {k: base64.b64encode(v).decode("ascii") for k, v in cache.items()}
            ).encode("utf-8")
            nonce = secrets.token_bytes(12)
            ciphertext = AESGCM(key).encrypt(nonce, plaintext, FILE_MAGIC)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_bytes(FILE_MAGIC_V2 + salt + nonce + ciphertext)
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
        # B05-003：load+save 原子化，避免并发读写交错
        with self._io_lock:
            cache = self._load()
            cache[key] = value.encode("utf-8")
            self._save()

    def delete(self, key: str) -> bool:
        with self._io_lock:
            cache = self._load()
            if key not in cache:
                return False
            del cache[key]
            self._save()
            return True

    def keys(self) -> list[str]:
        return list(self._load())

    def has_plaintext_value(self) -> bool:
        """当前文件是否为明文/未加密状态（调试/自检用）。

        B05-002：原命名 contains_plaintext 有误导（易被理解为"包含明文值"），
        实际语义是"文件未经加密（magic 缺失或过短）"。
        """
        if not self.path.is_file():
            return False
        raw = self.path.read_bytes()
        return not (raw.startswith(FILE_MAGIC) or raw.startswith(FILE_MAGIC_V2))

    # -- 单 blob 加解密（供 cookie 等独立文件加密，S2.2.3） --------------------

    def encrypt(self, data: bytes) -> bytes:
        """加密任意字节流，返回自包含 blob（magic+nonce+密文）。"""
        _ensure_crypto()
        key = self._master_key()
        nonce = secrets.token_bytes(12)
        return FILE_MAGIC + nonce + AESGCM(key).encrypt(nonce, data, FILE_MAGIC)  # type: ignore[misc]

    def decrypt(self, blob: bytes) -> bytes:
        """解密 ``encrypt`` 产生的 blob。"""
        if not blob.startswith(FILE_MAGIC):
            raise SecretsStoreError("secrets blob 格式损坏")
        _ensure_crypto()
        key = self._master_key()
        nonce = blob[len(FILE_MAGIC) : len(FILE_MAGIC) + 12]
        ciphertext = blob[len(FILE_MAGIC) + 12 :]
        try:
            return AESGCM(key).decrypt(nonce, ciphertext, FILE_MAGIC)  # type: ignore[misc]
        except Exception as exc:
            raise SecretsStoreError("secrets blob 解密失败（密钥不匹配?）") from exc
