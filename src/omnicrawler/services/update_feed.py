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
      "version": "<X.Y.Z>",
      "published_at": "<ISO8601>",
      "notes": "本版要点……",
      "assets": {
        "windows-standard": {
          "name": "OmniCrawler-<X.Y.Z>-Windows-Portable-Standard.zip",
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
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from ..core.versions import is_newer, version_key
from .component_manager import _verify_ed25519

#: 更新源文档的固定文件名（与市场 ``catalog.json`` 一样，名字是契约的一部分）。
FEED_FILENAME = "update.json"

#: 未显式配置 ``self_update.feed_url`` 时使用的默认更新源。
#:
#: 这是 GitHub 的**「最新 release 资产」固定链接**（302 到最新那版的该资产，实测过）：
#: 把 ``update.json`` 作为每版都上传的 Release 资产，客户端取
#: ``<feed_url>/update.json`` 即永远拿到最新清单——**免版本号、免 API、零额外托管**。
#: 走 ``github.com`` ⇒ 大陆网络需镜像；清单带 sha256，**镜像不必可信**（只承担带宽）。
DEFAULT_FEED_URL = "https://github.com/Starlife-creator/omnicrawler/releases/latest/download"

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
class UpdateFile:
    """载荷内单个文件的目标状态（相对应用根的路径 → 内容哈希 + 体积）。"""

    sha256: str
    size: int


@dataclass(frozen=True)
class FullFallback:
    """「最近一次带全量包的发布」。

    小版本发布**不重建全量包**时（§A.13），本机版本不在增量基线内的用户从这里取全量，
    否则会被永久卡住。``base``＋``name`` 与资产同构（base 指向那次发布的下载基址）。
    """

    version: str
    base: str
    name: str
    sha256: str
    size: int


@dataclass(frozen=True)
class UpdateFeed:
    version: str
    published_at: str = ""
    notes: str = ""
    assets: Mapping[str, UpdateAsset] = field(default_factory=dict)
    #: 载荷逐文件清单（相对应用根）。为空 = 只提供整包，无法做文件级跳过。
    payload_files: Mapping[str, UpdateFile] = field(default_factory=dict)
    #: 载荷基址（缺省＝feed 基址）。逐文件下载/变更包都相对它解析。
    payload_base: str = ""
    #: 变更包：``旧版本号 → 资源``（只含相对该旧版变化的文件）。
    payload_delta: Mapping[str, UpdateAsset] = field(default_factory=dict)
    #: 新版本**已移除**的路径（逐文件差异必须显式声明删除，否则旧文件会残留）。
    payload_deleted: tuple[str, ...] = ()
    #: 最近一次带全量包的发布（本版 assets 缺失时的兜底；None＝不提供）。
    full_fallback: FullFallback | None = None

    def asset_for(self, platform: str, edition: str) -> UpdateAsset | None:
        return self.assets.get(asset_key(platform, edition))


@dataclass(frozen=True)
class PayloadPlan:
    """本机载荷与目标清单的比对结果（判定"要下哪些"，**不涉及任何写入**）。"""

    to_fetch: tuple[str, ...]
    unchanged: int
    missing_locally: tuple[str, ...]
    fetch_bytes: int
    present_deleted: tuple[str, ...]

    @property
    def needs_download(self) -> int:
        return len(self.to_fetch) + len(self.missing_locally)


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
    """把信任根来源解成原始 32 字节：**PEM 文本 / PEM 文件路径** / ``hex:`` / base64。

    空值返回 ``None``（调用方据此判"未配置 ⇒ 禁用"）。**不给默认值、不从网络取**：
    信任根只能由使用者显式提供。解不出来即报错（而不是静默当作未配置）。

    判定顺序是确定性的（不靠"看起来像"）：
    ① ``-----BEGIN`` 开头 ⇒ 内联 PEM；② **该路径的文件确实存在** ⇒ 文件路径；
    ③ 以 ``.pem``/``.pub`` 结尾 ⇒ 按路径处理（**不存在即报错**，不回落成 base64）；
    ④ ``hex:`` 前缀 ⇒ 十六进制；⑤ 其余 ⇒ base64（#101 的原始形态，保持兼容）。

    ★ 顺序里"先文件存在"放在"看后缀"之前，是因为 base64 也可能含 ``/`` 与 ``+``，
    仅凭字符无法区分路径与 base64 —— 用文件系统事实与显式后缀来判定才可复现。
    """
    text = (value or "").strip()
    if not text:
        return None

    if text.startswith("-----BEGIN"):
        raw = _pem_public_key_bytes(text, source="内联 PEM 信任根")
    elif _is_existing_file(text) or text.lower().endswith((".pem", ".pub")):
        path = Path(text).expanduser()
        if not path.is_file():
            raise UpdateFeedError(f"更新信任根文件不存在: {path}")
        raw = _pem_public_key_bytes(
            path.read_text(encoding="utf-8"), source=str(path)
        )
    elif text.startswith("hex:"):
        try:
            raw = bytes.fromhex(text[4:])
        except ValueError as exc:
            raise UpdateFeedError(f"更新信任根不是合法十六进制: {exc}") from exc
    else:
        try:
            raw = base64.b64decode(text, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise UpdateFeedError(
                "更新信任根既不是 PEM/文件路径，也不是合法 base64（ed25519 公钥 32 字节）"
            ) from exc

    if len(raw) != _ED25519_KEY_BYTES:
        raise UpdateFeedError(
            f"更新信任根长度不对：ed25519 公钥应为 {_ED25519_KEY_BYTES} 字节，实际 {len(raw)}"
        )
    return raw


def _is_existing_file(value: str) -> bool:
    try:
        return Path(value).expanduser().is_file()
    except OSError:
        return False


def _pem_public_key_bytes(pem: str, *, source: str) -> bytes:
    """PEM → 原始公钥字节（复用 `plugins.identity` 的解析，避免第二套 PEM 处理）。"""
    from ..plugins.identity import public_key_bytes_from_pem

    try:
        return public_key_bytes_from_pem(pem)
    except Exception as exc:
        raise UpdateFeedError(f"更新信任根 PEM 解析失败（{source}）: {exc}") from exc


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
    if raw_assets is not None and not isinstance(raw_assets, dict):
        raise UpdateFeedError("更新源文档的 assets 必须是对象")
    # ★ assets 允许缺失/为空——小版本发布可以只带变更包 + full_fallback（不重建全量包）；
    #   但两者至少要有一个，否则客户端没有任何可下载的东西。
    has_full_fallback = isinstance(document.get("full_fallback"), dict)
    if not raw_assets and not has_full_fallback:
        raise UpdateFeedError(
            "更新源文档既没有 assets 也没有 full_fallback ⇒ 客户端无任何可下载内容"
        )

    assets: dict[str, UpdateAsset] = {}
    for key, value in (raw_assets or {}).items():
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

    payload_files, payload_base, payload_delta, payload_deleted = _parse_payload(document)
    full_fallback = _parse_full_fallback(document)
    return UpdateFeed(
        version=version,
        published_at=str(document.get("published_at") or ""),
        notes=str(document.get("notes") or ""),
        assets=assets,
        payload_files=payload_files,
        payload_base=payload_base,
        payload_delta=payload_delta,
        payload_deleted=payload_deleted,
        full_fallback=full_fallback,
    )


def _parse_payload(
    document: Mapping[str, Any],
) -> tuple[dict[str, UpdateFile], str, dict[str, UpdateAsset], tuple[str, ...]]:
    """解析可选的 ``payload`` 段：逐文件清单 + 基址 + 变更包 + 删除清单。

    ★ 这一段是"能否只下差异"的全部依据，所以**逐条严格校验**（越界路径、非法哈希、
    体积非法都直接拒绝整份文档）—— 宁可拒绝，也不要拿一份只信了一半的清单去落地。
    """
    raw_payload = document.get("payload")
    if raw_payload is None:
        return {}, "", {}, ()
    if not isinstance(raw_payload, dict):
        raise UpdateFeedError("payload 必须是对象")

    files: dict[str, UpdateFile] = {}
    raw_files = raw_payload.get("files")
    if raw_files is not None:
        if not isinstance(raw_files, dict) or not raw_files:
            raise UpdateFeedError("payload.files 必须是非空对象")
        for name, value in raw_files.items():
            relative = str(name)
            if not is_safe_asset_name(relative):
                raise UpdateFeedError(f"payload.files 的路径不合法: {relative}")
            if not isinstance(value, dict):
                raise UpdateFeedError(f"payload.files.{relative} 必须是对象")
            sha256 = str(value.get("sha256") or "").strip().lower()
            if not _SHA256_RE.match(sha256):
                raise UpdateFeedError(f"payload.files.{relative}.sha256 必须是 64 位十六进制")
            size = value.get("size")
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise UpdateFeedError(f"payload.files.{relative}.size 必须是非负整数")
            files[relative] = UpdateFile(sha256=sha256, size=size)

    delta: dict[str, UpdateAsset] = {}
    raw_delta = raw_payload.get("delta")
    if raw_delta is not None:
        if not isinstance(raw_delta, dict):
            raise UpdateFeedError("payload.delta 必须是对象")
        for from_version, value in raw_delta.items():
            key = str(from_version)
            if not isinstance(value, dict):
                raise UpdateFeedError(f"payload.delta.{key} 必须是对象")
            name = str(value.get("name") or "").strip()
            if not name or not is_safe_asset_name(name):
                raise UpdateFeedError(f"payload.delta.{key}.name 必须是相对文件名")
            sha256 = str(value.get("sha256") or "").strip().lower()
            if not _SHA256_RE.match(sha256):
                raise UpdateFeedError(f"payload.delta.{key}.sha256 必须是 64 位十六进制")
            size = value.get("size")
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise UpdateFeedError(f"payload.delta.{key}.size 必须是非负整数")
            delta[key] = UpdateAsset(key=key, name=name, sha256=sha256, size=size)

    deleted: list[str] = []
    raw_deleted = raw_payload.get("deleted")
    if raw_deleted is not None:
        if not isinstance(raw_deleted, list):
            raise UpdateFeedError("payload.deleted 必须是数组")
        for item in raw_deleted:
            relative = str(item)
            if not is_safe_asset_name(relative):
                raise UpdateFeedError(f"payload.deleted 的路径不合法: {relative}")
            if relative in files:
                raise UpdateFeedError(f"payload 自相矛盾：{relative} 同时在 files 与 deleted 里")
            deleted.append(relative)

    return files, str(raw_payload.get("base_url") or "").strip(), delta, tuple(deleted)


def _parse_full_fallback(document: Mapping[str, Any]) -> FullFallback | None:
    """解析可选的顶层 ``full_fallback``：最近一次带全量包的发布（版本+基址+资产+哈希）。"""
    raw = document.get("full_fallback")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise UpdateFeedError("full_fallback 必须是对象")
    version = str(raw.get("version") or "").strip()
    base = str(raw.get("base") or "").strip()
    name = str(raw.get("name") or "").strip()
    sha256 = str(raw.get("sha256") or "").strip().lower()
    size = raw.get("size")
    if not version or not base or not name:
        raise UpdateFeedError("full_fallback 缺少 version/base/name")
    if not _SHA256_RE.match(sha256):
        raise UpdateFeedError("full_fallback.sha256 必须是 64 位十六进制")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise UpdateFeedError("full_fallback.size 必须是非负整数")
    if not is_safe_asset_name(name):
        raise UpdateFeedError(f"full_fallback.name 不合法: {name}")
    return FullFallback(version=version, base=base, name=name, sha256=sha256, size=size)


def plan_payload(
    feed: UpdateFeed,
    *,
    local_hashes: Mapping[str, str],
    exists: Callable[[str], bool] | None = None,
) -> PayloadPlan:
    """比对"目标清单 vs 本机文件" ⇒ 要下哪些、要下多少字节、哪些已移除。

    ``local_hashes`` 由调用方算（**必须是本机文件的实际哈希**），缺失的路径用不出现在
    映射里表示"本机没有"。判据：**哈希相等才跳过** —— 不看体积、不看时间戳
    （体积相同内容不同必须重下；这是"偶然相同 ≠ 正确"的直接体现）。

    ``exists`` 用于判断"``payload.deleted`` 里的路径本机是否还在"。它**不能**用
    ``local_hashes`` 代替：被删除的路径本来就不在目标清单里，因此永远不会出现在
    ``local_hashes`` 中 —— 那样写会让"需要清理的残留文件"恒为空（实测踩过）。
    缺省回退为"在 ``local_hashes`` 里"，仅对不依赖删除语义的调用方成立。
    """
    to_fetch: list[str] = []
    missing: list[str] = []
    unchanged = 0
    fetch_bytes = 0
    for relative, entry in sorted(feed.payload_files.items()):
        current = local_hashes.get(relative)
        if current is None:
            missing.append(relative)
            fetch_bytes += entry.size
        elif current != entry.sha256:
            to_fetch.append(relative)
            fetch_bytes += entry.size
        else:
            unchanged += 1
    is_present = exists or (lambda relative: relative in local_hashes)
    present_deleted = tuple(
        relative for relative in feed.payload_deleted if is_present(relative)
    )
    return PayloadPlan(
        to_fetch=tuple(to_fetch),
        unchanged=unchanged,
        missing_locally=tuple(missing),
        fetch_bytes=fetch_bytes,
        present_deleted=present_deleted,
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
