"""Offline ed25519 plugin signing/verification primitives.

Private keys are generated and held ONLY by the offline signing host (see
``tools/sign_plugin.py``); the runtime only ever sees the public key (the trust
root). This module depends solely on ``cryptography``, which is already a hard
dependency, so it adds no new packages and works fully offline — fitting the
project's portable, offline build.

Design notes (cold-key principle):
- The signing private key never lives in the repo, build outputs, or the
  portable zip. It is generated on the operator's cold-storage location and
  moved away immediately.
- Verification only needs the public key, so the shipped product cannot leak
  the private key.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# cryptography is an optional dependency (pyproject.toml → [security]).
# Lazy-import to allow importing signing without cryptography installed
# (e.g. e2e CI where only html/pdf/browser/dev extras are present).
_cryptography_imported = False
InvalidSignature: Any
serialization: Any
Ed25519PrivateKey: Any
Ed25519PublicKey: Any


def _ensure_crypto() -> None:
    """Import cryptography primitives on first use; raise a clear error if missing."""
    global InvalidSignature, serialization, Ed25519PrivateKey, Ed25519PublicKey  # noqa: PLW0603
    global _cryptography_imported  # noqa: PLW0603
    if _cryptography_imported:
        return
    try:
        from cryptography.exceptions import InvalidSignature as _InvalidSignature
        from cryptography.hazmat.primitives import serialization as _serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey as _Ed25519PrivateKey,
        )
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey as _Ed25519PublicKey,
        )
    except ModuleNotFoundError as exc:
        raise ImportError(
            "需要安装 cryptography 才能使用插件签名验证。请运行: pip install 'omnicrawler-platform[security]'"
        ) from exc
    InvalidSignature = _InvalidSignature
    serialization = _serialization
    Ed25519PrivateKey = _Ed25519PrivateKey
    Ed25519PublicKey = _Ed25519PublicKey
    _cryptography_imported = True


ALGORITHM = "ed25519"


class PluginSignatureError(PermissionError):
    """Raised when a plugin fails signature verification (fail-closed)."""


def generate_keypair() -> tuple[bytes, bytes]:
    """Return ``(private_pem, public_pem)`` as PEM bytes.

    The caller is responsible for storing the private key on cold storage and
    never committing it.
    """
    _ensure_crypto()

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


def _load_private_key(private_pem: bytes) -> Ed25519PrivateKey:
    _ensure_crypto()
    key = serialization.load_pem_private_key(private_pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError("插件签名私钥必须是 ed25519 密钥")
    return key


def _load_public_key(trust_source: str) -> Ed25519PublicKey:
    _ensure_crypto()
    candidate = trust_source.strip()
    if not candidate:
        raise ValueError("未配置插件信任根公钥")
    maybe = Path(candidate)
    if maybe.is_file():
        candidate = maybe.read_text(encoding="utf-8").strip()
    key = serialization.load_pem_public_key(candidate.encode("utf-8"))
    if not isinstance(key, Ed25519PublicKey):
        raise TypeError("信任根公钥必须是 ed25519 公钥")
    return key


def sign_bytes(data: bytes, private_pem: bytes) -> bytes:
    """Produce a raw detached signature over ``data``."""

    return _load_private_key(private_pem).sign(data)


def sign_file(path: str | Path, private_pem: bytes) -> Path:
    """Write a detached ``<plugin>.sig`` next to ``path`` and return its path."""

    signature = sign_bytes(Path(path).read_bytes(), private_pem)
    sig_path = Path(path).with_suffix(Path(path).suffix + ".sig")
    sig_path.write_bytes(signature)
    return sig_path


def load_public_key(trust_source: str) -> Ed25519PublicKey:
    """Load the trust-root public key from a PEM path or inline PEM (public API)."""
    return _load_public_key(trust_source)


def verify_bytes(data: bytes, signature: bytes, trust_source: str) -> bool:
    """Return True iff ``signature`` is a valid ed25519 signature of ``data``."""

    try:
        _load_public_key(trust_source).verify(signature, data)
    except (InvalidSignature, ValueError, TypeError):
        return False
    return True


def verify_plugin(path: str | Path, trust_source: str) -> tuple[bool, str]:
    """Verify a plugin file against the configured trust root.

    Returns ``(ok, reason)``. The loader treats a False result as fail-closed
    when a trust root is configured. A missing trust root is reported as
    ``(False, "未配置插件信任根公钥")`` so the caller can decide to warn.
    """

    try:
        public_key = _load_public_key(trust_source)
    except ValueError:
        return False, "未配置插件信任根公钥"
    sig_path = Path(path).with_suffix(Path(path).suffix + ".sig")
    if not sig_path.is_file():
        return False, "未找到插件签名文件 (.sig)"
    signature = sig_path.read_bytes()
    try:
        public_key.verify(signature, Path(path).read_bytes())
    except (InvalidSignature, ValueError, TypeError):
        return False, "签名校验失败（文件可能被篡改）"
    return True, "verified"
