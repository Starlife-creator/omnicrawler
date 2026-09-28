"""应用自更新：更新源文档（``update.json``）的解析、验签与版本判定。

**纯逻辑，不做 I/O** —— 取回由调用方（CLI/GUI 交付层）负责，便于离线单测。

issue #88 的第一片：把「检查更新」的判据集中到一处，并把两条设计红线写死在代码里
（都能被"装回缺口 ⇒ 必须转红"的用例钉住）：

1. ★ **没有默认信任根、没有「跳过验签」开关**。未配置
   ``self_update.trusted_public_key`` ⇒ 直接 :class:`UpdateFeedError`（fail-closed），
   而不是"降级为不校验"。与 §10.7「不设跳过签名验证开关」同一条原则。
2. ★ **不绑定任何托管方**。更新源是一个**配置的基址**（``self_update.feed_url``，
   远程目录或本地目录均可），文档名固定为 ``update.json``；资产路径是**相对基址**的
   文件名（与市场 ``catalog.json`` 的模型一致）。core 因此不认识 GitHub Releases
   或任何特定服务的 API（§10.7 明列「在 core 硬编码 GitHub API 调用」为不做项），
   本地目录形态也让离线/内网镜像开箱可用。

文档格式（``signature`` 覆盖**除它本身外**的规范化 JSON，规范化方式与
``services/updater.UpgradeManager`` 的升级包**逐字一致**，因此两者可共用同一信任根）::

    {
      "version": "0.15.0",
      "published_at": "2026-09-28T00:00:00Z",
      "notes": "本版要点……",
      "assets": {
        "windows-standard": {
          "name": "OmniCrawler-0.15.0-Windows-Portable-Standard.zip",
          "sha256": "<64 位十六进制>",
          "size": 123456789
        }
      },
      "signature": "<base64(ed25519)>"
    }
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from ..core.versions import is_newer, version_key
from .component_manager import _verify_ed25519

#: 更新源文档的固定文件名（与市场 ``catalog.json`` 一样，名字是契约的一部分）。
FEED_FILENAME = "update.json"

#: 可选版本后缀（``Standard`` / ``Full``）。空串表示"不区分版本"。
DEFAULT_EDITION = "Standard"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ED25519_KEY_BYTES = 32


class UpdateFeedError(RuntimeError):
    """更新源不可用：未配置信任根 / 缺签名 / 验签失败 / 字段非法 —— 一律 fail-closed。"""


@dataclass(frozen=True)
class UpdateAsset:
    key: str
    name: str
    sha256: str
    size: int


@dataclass(frozen=True)
class UpdateFeed:
    version: str
    published_at: str = ""
    notes: str = ""
    assets: Mapping[str, UpdateAsset] = field(default_factory=dict)

    def asset_for(self, platform: str, edition: str) -> UpdateAsset | None:
        return self.assets.get(asset_key(platform, edition))


@dataclass(frozen=True)
class UpdateCheck:
    """检查结论。``status`` 只有三种，避免"看起来正常"掩盖配置缺失。"""

    status: str  # disabled | update-available | up-to-date
    detail: str
    current_version: str
    latest_version: str = ""
    asset: UpdateAsset | None = None
    notes: str = ""

    @property
    def update_available(self) -> bool:
        return self.status == "update-available"


def detect_platform() -> str:
    """当前平台键（与发布资产命名约定 ``-Windows-`` / ``-Linux-`` / ``-macOS-`` 对应）。"""
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def asset_key(platform: str, edition: str) -> str:
    """资产键：``<platform>-<edition>``（小写，便于与文档键逐字比对）。"""
    return f"{platform.strip().lower()}-{edition.strip().lower()}"


def is_safe_asset_name(name: str) -> bool:
    """资产名必须是**相对**路径且不含上跳 —— 与升级包的 ``_safe_upgrade_path`` 同一边界。"""
    path = PurePosixPath(name.strip())
    return bool(path.parts) and not path.is_absolute() and ".." not in path.parts


def canonical_feed_bytes(document: Mapping[str, Any]) -> bytes:
    """规范化待验签字节（与 ``UpgradeManager.stage`` 同一套：sort_keys + 紧凑分隔符）。"""
    return json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def decode_public_key(value: str | None) -> bytes | None:
    """把配置里的公钥解成原始字节：base64（默认）或 64 位十六进制。

    空值返回 ``None``（调用方据此判"未配置 ⇒ 禁用"）。**不给默认值、不从网络取**：
    信任根只能由使用者显式提供。解不出来即报错（而不是静默当作未配置）。
    """
    text = (value or "").strip()
    if not text:
        return None
    candidate = text
    if candidate.startswith("hex:"):
        candidate = candidate[4:]
        try:
            raw = bytes.fromhex(candidate)
        except ValueError as exc:
            raise UpdateFeedError(f"更新信任根不是合法十六进制: {exc}") from exc
    else:
        try:
            raw = base64.b64decode(candidate, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise UpdateFeedError(
                "更新信任根不是合法 base64（ed25519 公钥，32 字节）"
            ) from exc
    if len(raw) != _ED25519_KEY_BYTES:
        raise UpdateFeedError(
            f"更新信任根长度不对：ed25519 公钥应为 {_ED25519_KEY_BYTES} 字节，实际 {len(raw)}"
        )
    return raw


def verify_feed_document(raw: bytes, *, trusted_public_key: bytes | None) -> UpdateFeed:
    """校验并解析更新源文档；任何一步不成立都抛 :class:`UpdateFeedError`。"""
    if not trusted_public_key:
        raise UpdateFeedError(
            "未配置更新信任根（self_update.trusted_public_key）：应用自更新已禁用。"
            "本命令不提供跳过验签的开关 —— 安全不降级。"
        )
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateFeedError(f"更新源文档不是合法 JSON（需 UTF-8）: {exc}") from exc
    if not isinstance(parsed, dict):
        raise UpdateFeedError("更新源文档顶层必须是对象")

    document = dict(parsed)
    signature = str(document.pop("signature", "") or "")
    if not signature:
        raise UpdateFeedError("更新源文档缺少 signature 字段（不接受未签名文档）")
    try:
        _verify_ed25519(trusted_public_key, canonical_feed_bytes(document), signature)
    except Exception as exc:  # cryptography 的 InvalidSignature 等一律归为验签失败
        # InvalidSignature 的 str() 是空串 —— 直接透传会得到"验签失败: "这种没信息量的告警。
        detail = str(exc).strip() or type(exc).__name__
        raise UpdateFeedError(
            f"更新源文档验签失败（{detail}）：文档被改动，或与配置的信任根不匹配"
        ) from exc
    return _parse_document(document)


def _parse_document(document: Mapping[str, Any]) -> UpdateFeed:
    version = str(document.get("version") or "").strip()
    if not version or not version_key(version):
        raise UpdateFeedError("更新源文档的 version 缺失或不可比较")

    raw_assets = document.get("assets")
    if not isinstance(raw_assets, dict) or not raw_assets:
        raise UpdateFeedError("更新源文档的 assets 缺失或为空")

    assets: dict[str, UpdateAsset] = {}
    for key, value in raw_assets.items():
        asset_key_name = str(key)
        if not isinstance(value, dict):
            raise UpdateFeedError(f"assets.{asset_key_name} 必须是对象")
        name = str(value.get("name") or "").strip()
        if not name or not is_safe_asset_name(name):
            raise UpdateFeedError(
                f"assets.{asset_key_name}.name 必须是相对文件名（不得绝对路径或含 ..）"
            )
        sha256 = str(value.get("sha256") or "").strip().lower()
        if not _SHA256_RE.match(sha256):
            raise UpdateFeedError(f"assets.{asset_key_name}.sha256 必须是 64 位十六进制")
        size = value.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise UpdateFeedError(f"assets.{asset_key_name}.size 必须是非负整数")
        assets[asset_key_name] = UpdateAsset(
            key=asset_key_name, name=name, sha256=sha256, size=size
        )

    return UpdateFeed(
        version=version,
        published_at=str(document.get("published_at") or ""),
        notes=str(document.get("notes") or ""),
        assets=assets,
    )


def check_feed(
    feed: UpdateFeed, *, current_version: str, platform: str, edition: str
) -> UpdateCheck:
    """把"已装版本 vs 更新源版本"的判定集中在此（含"有新版本但本平台没有资产"）。"""
    if not is_newer(feed.version, current_version):
        return UpdateCheck(
            status="up-to-date",
            detail=f"已是最新（当前 {current_version}）",
            current_version=current_version,
            latest_version=feed.version,
        )
    key = asset_key(platform, edition)
    asset = feed.asset_for(platform, edition)
    if asset is None:
        return UpdateCheck(
            status="update-available",
            detail=(
                f"有新版本 {feed.version}，但更新源未提供 {key} 资产"
                f"（可用：{', '.join(sorted(feed.assets)) or '无'}）"
            ),
            current_version=current_version,
            latest_version=feed.version,
            notes=feed.notes,
        )
    return UpdateCheck(
        status="update-available",
        detail=f"可更新到 {feed.version}（{asset.name}，{asset.size} 字节）",
        current_version=current_version,
        latest_version=feed.version,
        asset=asset,
        notes=feed.notes,
    )


def feed_path(base: str | Path) -> str:
    """本地基址下的文档路径（远程基址请用 ``market_client.fetch_resource`` 拼接）。"""
    return str(Path(base) / FEED_FILENAME)
