#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""维护者工具共用件：读「已签名清单」与信任根。

为什么独立成模块：`build_update_manifest.py` 与 `build_update_delta.py` **都**要读上一版的
已签名清单（前者算"已移除"清单，后者算"哪些文件变了"）。这段逻辑是**安全判据**
（必须验签、必须核对声明的版本号），复制两份迟早会漂移成"一份验签一份不验"。
"""

from __future__ import annotations

import hashlib
import json
import sys
import tarfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from omnicrawler.services.update_feed import (  # noqa: E402
    FEED_FILENAME,
    UpdateFeed,
    decode_public_key,
    verify_feed_document,
)

#: 供两个工具共用（`build_update_delta.py` 按 (平台, 版本) 逐级回退取基线清单时要用）。
__all__ = [
    "DEFAULT_TRUST_ROOT",
    "FEED_FILENAME",
    "PayloadEntry",
    "cumulative_deleted",
    "iter_archive_members",
    "load_payload_entries",
    "load_signed_manifest",
    "read_source",
    "reject_protected_top_level",
    "require_single_root",
    "resolve_trusted_key",
    "sha256_of",
    "split_root",
]

#: 随包内置的更新信任根（与市场信任根**刻意是两把钥匙**）。
DEFAULT_TRUST_ROOT = "configs/update_trust.pub.pem"

#: 单独取一份清单的超时（秒）。清单只有几十~几百 KB，60s 足够且不至于挂死 CI。
FETCH_TIMEOUT = 60

#: 绝不进清单的顶层目录（用户数据 / 标记）。载荷来自构建产物，本不该含它们；
#: 真含了说明打包错了 ⇒ 这里**直接拒绝**而不是默默漏掉（静默漏掉会让清单与实际归档不一致）。
FORBIDDEN_TOP_LEVEL = {
    "work",
    "data",
    "output",
    "logs",
    ".omnicrawler",
    "PORTABLE.flag",
    "portable.flag",
}

#: 构建垃圾，不参与清单（否则每次构建都会产生"变化"）。
SKIP_SUFFIXES = {".pyc", ".pyo"}
SKIP_DIRS = {"__pycache__", ".pytest_cache"}


@dataclass(frozen=True)
class PayloadEntry:
    """载荷里的一个文件：``sha256`` + ``size`` + POSIX 权限位（``None``＝无记录）。"""

    sha256: str
    size: int
    mode: int | None = None


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_stream(handle) -> tuple[str, int]:  # type: ignore[no-untyped-def]
    """流式算哈希，返回 ``(hexdigest, 字节数)``。"""
    digest = hashlib.sha256()
    total = 0
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        total += len(chunk)
        digest.update(chunk)
    return digest.hexdigest(), total


def reject_protected_top_level(relative: str, *, origin: str) -> None:
    top = relative.split("/", 1)[0]
    if top.lower() in FORBIDDEN_TOP_LEVEL:
        raise SystemExit(
            f"载荷里出现受保护顶层路径 {top}/（{origin}）—— "
            "构建产物不该包含用户数据目录，请检查打包步骤"
        )


def split_root(name: str, *, archive_name: str) -> tuple[str, str]:
    """把归档成员名拆成 ``(顶层目录名, 相对应用根的路径)``。

    ★ 顶层目录名要**回传**而不是就地丢掉：便携包只该有**一个**顶层目录
    （`OmniCrawler/`）。出现两个顶层时，"逐成员剥掉第一段"会把两棵树扁平混在一起
    （目录结构被静默改写）—— 所以调用方必须收集所有顶层再校验唯一性，这里只负责拆。
    """
    parts = [part for part in name.replace("\\", "/").split("/") if part not in ("", ".")]
    if len(parts) < 2:
        raise SystemExit(f"归档成员不在顶层目录内（无法剥根）: {archive_name}!{name}")
    relative = "/".join(parts[1:])
    if relative:
        reject_protected_top_level(relative, origin=f"{archive_name}!{name}")
    return parts[0], relative


def require_single_root(roots: set[str], *, what: str) -> None:
    """顶层目录必须唯一（否则"剥根"是猜的，清单路径会错位）。"""
    if len(roots) != 1:
        raise SystemExit(f"{what} 顶层目录不唯一 {sorted(roots)} —— 便携包应当只有单个 <根>/ 顶层")


def iter_archive_members(archive: Path):  # type: ignore[no-untyped-def]
    """流式遍历归档成员：产出 ``(顶层目录名, 相对路径, 权限位, 读取器)``。

    ★ tar.xz 是**单一 LZMA 流**（偏移不可随机访问）⇒ 只能顺序单次遍历。
    因此调用方必须**逐个成员就能判定去留**（本工具的判据恰好满足：
    "在不在基线里" ∧ "哈希是否相同"都不需要先看齐全部成员）。
    zip 走同一条代码路径（顺序遍历），两种容器行为一致、少一条分叉。
    """
    if zipfile.is_zipfile(archive):
        # ★ 按**魔数**判容器而不是按文件名后缀：后缀与实际格式不符时不至于把 zip 当 tar 读
        #   （实测踩过：测试里把 zip 写成 `.tar.xz` 名字 ⇒ tarfile 报"不是 lzma 文件"）。
        with zipfile.ZipFile(archive) as handle:
            for info in handle.infolist():
                if info.is_dir():
                    continue
                root, relative = split_root(info.filename, archive_name=archive.name)
                if not relative:
                    continue
                mode = (info.external_attr >> 16) & 0o777 or None
                with handle.open(info) as stream:
                    yield root, relative, mode, stream
    else:
        with tarfile.open(archive, "r:*") as handle:
            for member in handle:
                if not member.isfile():
                    continue
                root, relative = split_root(member.name, archive_name=archive.name)
                if not relative:
                    continue
                stream = handle.extractfile(member)
                if stream is None:  # pragma: no cover - isfile() 为真时必有流
                    continue
                yield root, relative, (member.mode & 0o777) or None, stream


def walk_payload_dir(root: Path) -> dict[str, Path]:
    """遍历**解压后的**载荷目录（跳过构建垃圾，拒绝受保护顶层）。"""
    if not root.is_dir():
        raise SystemExit(f"载荷目录不存在: {root}")
    files: dict[str, Path] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        reject_protected_top_level(relative.as_posix(), origin=str(path))
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        files[relative.as_posix()] = path
    if not files:
        raise SystemExit(f"载荷目录里没有任何文件: {root}")
    return files


def hash_archive_payload(archive: Path) -> dict[str, PayloadEntry]:
    """从归档**本体**算逐文件条目（与用户拿到的字节逐字节一致）。

    为什么不从构建暂存目录算：客户端下载的是归档，清单必须与**归档里的字节**一致。
    从暂存目录算就多出一条"打包时排除了什么"的隐含约定（tar 排除 ``OmniCrawler/logs``、
    zip 排除顶层 ``logs/``…），两处一旦不同步，增量校验会**很晚**才失败（用户侧）。
    """
    if not archive.is_file():
        raise SystemExit(f"载荷归档不存在: {archive}")
    entries: dict[str, PayloadEntry] = {}
    roots: set[str] = set()
    for root, relative, _mode, stream in iter_archive_members(archive):
        roots.add(root)
        digest, size = sha256_stream(stream)
        entries[relative] = PayloadEntry(sha256=digest, size=size)
    if not entries:
        raise SystemExit(f"归档里没有任何文件: {archive}")
    require_single_root(roots, what=str(archive))
    return entries


def hash_dir_payload(root: Path) -> dict[str, PayloadEntry]:
    files = walk_payload_dir(root)
    return {
        relative: PayloadEntry(sha256=sha256_of(path), size=path.stat().st_size)
        for relative, path in files.items()
    }


def load_payload_entries(
    *, directory: str, archive: str, what: str
) -> dict[str, PayloadEntry]:
    """统一的载荷读取：目录（开发/回算）或归档（构建期，与发布字节一致）。"""
    if directory and archive:
        raise SystemExit(f"{what}：--payload-dir 与 --payload-archive 二选一")
    if archive:
        return hash_archive_payload(Path(archive).expanduser())
    if not directory:
        raise SystemExit(f"{what}：需要 --payload-dir 或 --payload-archive")
    return hash_dir_payload(Path(directory).expanduser())


def read_source(source: str) -> bytes:
    """读一份资源：``http(s)://`` 走网络，否则按本地路径（**不解释 file://**）。

    只做取件，不做判定 —— 判定一律交给 `verify_feed_document`。
    """
    text = str(source).strip()
    if text.startswith(("http://", "https://")):
        try:
            with urllib.request.urlopen(text, timeout=FETCH_TIMEOUT) as response:  # noqa: S310
                return bytes(response.read())
        except urllib.error.URLError as exc:
            raise SystemExit(f"取回失败 {text}: {exc}") from exc
    path = Path(text).expanduser()
    if not path.is_file():
        raise SystemExit(f"文件不存在: {path}")
    return path.read_bytes()


def resolve_trusted_key(explicit: str) -> bytes | None:
    """解析更新信任根：显式给了就用它（文件路径或 PEM/hex/base64 文本），否则用随包内置那份。

    **缺失即返回 None**（调用方必须据此报错退出）—— 绝不"验证不了就放行"。
    """
    value = str(explicit or "").strip()
    if not value:
        builtin = _REPO_ROOT / DEFAULT_TRUST_ROOT
        if not builtin.is_file():
            return None
        value = str(builtin)
    return decode_public_key(value)


def load_signed_manifest(
    spec: str, *, trusted_public_key: bytes | None
) -> tuple[str, UpdateFeed, bytes]:
    """读一份 ``<版本>=<路径|URL>`` 指定的**已签名清单**，返回 ``(声明版本, 清单, 原始字节)``。

    两条 fail-closed 判据：

    1. **必须验签**（用更新信任根）—— 变更包与"已移除"清单都直接依赖上一版的哈希，
       拿一份没验签的清单当基线，等于让中间人决定"用户该下什么"；
    2. **声明版本必须与文档里的 version 一致** —— 调用方按版本号做映射（`payload.delta[版本]`），
       错配会让客户端永远取不到那份变更包，而且错得很隐蔽。
    """
    declared, _, raw_source = str(spec).partition("=")
    declared = declared.strip()
    raw_source = raw_source.strip()
    if not declared or not raw_source:
        raise SystemExit(f"--previous-manifest/--baseline-manifest 需要 <版本>=<文件|URL>，收到: {spec}")
    if trusted_public_key is None:
        raise SystemExit("缺少更新信任根（configs/update_trust.pub.pem）⇒ 拒绝使用未验签的基线清单")
    body = read_source(raw_source)
    try:
        feed = verify_feed_document(body, trusted_public_key=trusted_public_key)
    except Exception as exc:  # 验签/字段/解析失败统一一种可读结论
        raise SystemExit(f"基线清单校验失败（{raw_source}）：{exc}") from exc
    if feed.version != declared:
        raise SystemExit(
            f"基线清单声明的版本不符：参数写的是 {declared}，文档里是 {feed.version}"
        )
    return declared, feed, body


def manifest_document(body: bytes) -> dict[str, object]:
    """把清单字节解析成 dict（只用于诊断输出；判定一律走 UpdateFeed）。"""
    try:
        value = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def cumulative_deleted(feeds: list[UpdateFeed], *, new_files: set[str]) -> list[str]:
    """把多份基线清单折算成**累积**的"已移除路径"。

    ``⋃ᵢ(prevᵢ.files ∪ prevᵢ.deleted) − new.files``

    为什么要取并集而不是只看最近一版：`payload.deleted` 是**一个扁平清单、无条件应用**，
    而从更老版本跳上来的用户手里还留着更早版本就已删过的文件 —— 只看最近一版会让那些
    残留**永远清不掉**（它们既不在新清单里，也不在"最近一版"的删除集里）。
    """
    removed: set[str] = set()
    for feed in feeds:
        removed |= set(feed.payload_files)
        removed |= set(feed.payload_deleted)
    return sorted(removed - new_files)
