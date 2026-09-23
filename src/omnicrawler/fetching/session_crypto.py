"""storage_state 快照的 AES-GCM 信封（《优化方案》§11.8 U5，2026-09-23 拍板启动）。

**不新造密码学**：密钥管理与 AEAD 全部复用 :mod:`omnicrawler.core.secrets_store`
（keyring / 热密钥双轨主密钥 + AES-GCM 信封惯例）。本模块只做**格式适配层**。

信封格式（**单点定义**，改这里就要同步全部守卫）::

    SESSION_MAGIC(8) + nonce(12) + AESGCM( zlib( json(storage_state) ), AAD=SESSION_MAGIC )

判定纪律（§11.8.3 反「假兼容」设计）：

- ``classify_snapshot`` 是**唯一**的明文/信封判定入口，**只看 magic 前缀**、不尝试解析；
- **magic 命中的信封若解密失败 ⇒ 报错引导重新登录，绝不回退明文解析**——
  「读明文也算成功」会让迁移名存实亡；
- 旧明文 JSON（无 magic）在读取时**一次性静默升级**为信封（``load_storage_state(migrate=True)``）。

文件名**不变**（仍是 ``<账户前缀+摘要>.playwright.json``）：路径真源
（:mod:`omnicrawler.fetching.session_state`）与 GUI「落盘位置」展示都不受影响；
格式由 magic 决定，不靠扩展名。

密钥丢失语义：``SecretsStore`` 主密钥变更/丢失 ⇒ 既有信封全部解不开 ⇒ 该快照作废、
**重新登录即恢复**。**刻意不建恢复通道**（建立恢复通道＝降低加密强度）。

缺包语义：``cryptography`` 缺失（裸源码安装且未装 ``[security]`` extra）⇒ **fail-closed
＋可行动提示**，与 :mod:`core.secrets_store` 同一口径。Standard/Full 便携构建的 extras
均含 ``security``（``build_windows.ps1``），冻结包内不受影响。
"""

from __future__ import annotations

import base64
import json
import os
import secrets as _random
import zlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..core.secrets_store import SecretsStore

__all__ = [
    "SESSION_KEY_NAME",
    "SESSION_MAGIC",
    "SessionCryptoError",
    "classify_snapshot",
    "load_storage_state",
    "open_storage_state",
    "save_storage_state",
    "seal_storage_state",
]

#: 信封 magic（8 字节，版本位 ``1``）——判定与 AAD 双用。
SESSION_MAGIC = b"OCSESS1\x01"
#: 会话密钥在 SecretsStore 里的键名（32 字节随机数，base64 存放）。
SESSION_KEY_NAME = "session_state_key"
_NONCE_SIZE = 12
_KEY_SIZE = 32


class SessionCryptoError(RuntimeError):
    """storage_state 信封操作失败（缺包/损坏/密钥不符）。"""


def _ensure_aesgcm() -> Any:
    """惰性导入 AESGCM；缺失时给**可行动**报错（与 secrets_store 同口径）。"""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ModuleNotFoundError as exc:  # pragma: no cover - 取决于安装组合
        raise SessionCryptoError(
            "需要安装 cryptography 才能加密登录会话快照。"
            "请运行: pip install 'omnicrawler-platform[security]'"
        ) from exc
    return AESGCM


def classify_snapshot(blob: bytes) -> str:
    """**唯一**的明文/信封判定入口：返回 ``"envelope"`` 或 ``"plaintext"``。

    只看 magic 前缀、**不做任何解析**——解析成败不参与判定（防假兼容）。
    """
    return "envelope" if blob.startswith(SESSION_MAGIC) else "plaintext"


def _session_aes_key(store: SecretsStore) -> bytes:
    """取（或首次创建）会话加密密钥：32 字节随机数，经 SecretsStore 双轨保管。"""
    raw = store.get(SESSION_KEY_NAME)
    if raw is None:
        raw = base64.b64encode(_random.token_bytes(_KEY_SIZE)).decode("ascii")
        store.set(SESSION_KEY_NAME, raw)
    try:
        key = base64.b64decode(raw, validate=True)
    except Exception as exc:  # noqa: BLE001 - base64 异常统一转可行动报错
        raise SessionCryptoError("会话加密密钥损坏（不是合法 base64）。") from exc
    if len(key) != _KEY_SIZE:
        raise SessionCryptoError("会话加密密钥长度异常，拒绝使用。")
    return key


def _store(store: SecretsStore | None) -> SecretsStore:
    return store if store is not None else SecretsStore()


def seal_storage_state(
    state: Mapping[str, Any],
    *,
    store: SecretsStore | None = None,
) -> bytes:
    """把 storage_state 字典封装为 AES-GCM 信封字节串。"""
    aesgcm = _ensure_aesgcm()
    nonce = _random.token_bytes(_NONCE_SIZE)
    plaintext = zlib.compress(
        json.dumps(dict(state), ensure_ascii=False).encode("utf-8")
    )
    key = _session_aes_key(_store(store))
    return SESSION_MAGIC + nonce + aesgcm(key).encrypt(nonce, plaintext, SESSION_MAGIC)


def open_storage_state(
    blob: bytes,
    *,
    store: SecretsStore | None = None,
) -> dict[str, Any]:
    """解开信封。**解密失败绝不回退明文解析**（防假兼容，§11.8.3）。"""
    if classify_snapshot(blob) != "envelope":
        raise SessionCryptoError("不是 storage_state 信封（magic 不符），拒绝解密。")
    aesgcm = _ensure_aesgcm()
    nonce_start = len(SESSION_MAGIC)
    nonce = blob[nonce_start : nonce_start + _NONCE_SIZE]
    ciphertext = blob[nonce_start + _NONCE_SIZE :]
    if len(nonce) != _NONCE_SIZE or not ciphertext:
        raise SessionCryptoError("storage_state 信封结构不完整。")
    key = _session_aes_key(_store(store))
    try:
        plaintext = aesgcm(key).decrypt(nonce, ciphertext, SESSION_MAGIC)
        data = json.loads(zlib.decompress(plaintext).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - 解密/解压/解析失败统一转可行动报错
        raise SessionCryptoError(
            "storage_state 信封解密失败（主密钥变更或文件损坏）。"
            "请删除该快照并重新登录一次。"
        ) from exc
    if not isinstance(data, dict):
        raise SessionCryptoError("storage_state 信封解开后的顶层不是对象。")
    return data


def _atomic_write(path: Path, blob: bytes) -> None:
    """原子写 + 0600（与登录捕获/桥接落盘的既有惯例一致）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(blob)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # Windows 无 POSIX 权限语义，靠 AES-GCM 兜底（secrets_store 同口径）


def save_storage_state(
    state: Mapping[str, Any],
    path: Path | str,
    *,
    store: SecretsStore | None = None,
) -> None:
    """**统一写入口**：seal → 原子写 → 0600。明文只存在于调用方内存。"""
    _atomic_write(Path(path), seal_storage_state(state, store=store))


def load_storage_state(
    path: Path | str,
    *,
    store: SecretsStore | None = None,
    migrate: bool = True,
) -> dict[str, Any]:
    """**统一读入口**（爬取加载 / 登录窗重启 / 会话桥三处共用）。

    - 信封 → 解密返回；
    - 旧明文 JSON → 解析返回；``migrate=True`` 时**同一次读取内重写为信封**（一次性静默升级）；
    - 文件不存在 → 原样抛 ``FileNotFoundError``（调用方自行按「无会话」处理）；
    - 损坏（有 magic 但解不开 / 无 magic 又不是合法 JSON 对象）→ ``SessionCryptoError``。
    """
    snapshot = Path(path)
    blob = snapshot.read_bytes()
    if classify_snapshot(blob) == "envelope":
        return open_storage_state(blob, store=store)
    try:
        data = json.loads(blob.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SessionCryptoError(
            "storage_state 快照损坏（既非加密信封也非合法 JSON）。"
            "请删除该快照并重新登录一次。"
        ) from exc
    if not isinstance(data, dict):
        raise SessionCryptoError("storage_state 快照的顶层不是对象。")
    if migrate:
        # 一次性静默升级：明文 → 信封（写进用户指南，不静默＝行为有据可查）。
        save_storage_state(data, snapshot, store=store)
    return data
