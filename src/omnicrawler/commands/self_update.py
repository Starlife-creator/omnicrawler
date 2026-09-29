"""应用自更新命令（交付层编排）：检查更新、下载并应用**已签名**的升级包。

设计要点（与 ``services/update_feed.py`` 的两条红线一致）：

* **信任根与更新源都来自配置**；缺任一 ⇒ 明确报"已禁用"（fail-closed），
  不降级为"不校验"、不提供跳过开关。
* **取回复用市场同一条受控出站读**（``market_client.fetch_resource``：egress 未注入即拒网、
  认 ``http.proxy``、DNS 逐地址尝试可回退 IPv4、重定向仍过策略与审计）。
  更新源按"**相对基址的资源**"组织（``update.json`` + 资产文件名），与 ``catalog.json``
  同模型 ⇒ 不再长出第二套代理/DNS/重定向处理（第二真源）。
* ``apply`` 会**覆盖应用文件**（受保护的工作区路径由 ``UpgradeManager`` 挡住）⇒ 属破坏性动作，
  必须显式 ``--yes/--apply``（判据沿用 ``core.safe_action``）；``--dry-run`` 只输出计划。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from .._version import __version__
from ..core.config import AppConfig, load_config
from ..core.runtime_paths import application_dir
from ..plugins.market_client import fetch_resource
from ..services.update_feed import (
    DEFAULT_EDITION,
    DEFAULT_FEED_URL,
    FEED_FILENAME,
    FullFallback,
    UpdateFeedError,
    asset_key,
    check_feed,
    decode_public_key,
    detect_platform,
    feed_filename,
    plan_payload,
    verify_feed_document,
)
from ..services.updater import UpgradeManager

#: 退出码四态（与 tools/ 下基准脚本同惯例：0 正常、1 有结果待处理、2 配置缺失、3 失败）。
EXIT_OK = 0
EXIT_UPDATE_AVAILABLE = 1
EXIT_DISABLED = 2
EXIT_FAILED = 3


def _self_update_section(config: AppConfig) -> dict[str, Any]:
    raw = getattr(config, "raw", None)
    section = raw.get("self_update") if isinstance(raw, dict) else None
    return section if isinstance(section, dict) else {}


def _resolve(config_path: str) -> tuple[dict[str, Any], AppConfig, bytes | None, str]:
    """读配置并解出（段, config, 信任根, 更新源基址）。信任根非法即抛错。

    信任根来源：``self_update.trusted_public_key`` 优先；**为空时回退内置信任根**
    ``configs/update_trust.pub.pem``（`AppConfig.update_trust_public_key`）——
    内置文件缺失则回退空串 ⇒ 调用方判"禁用"（fail-closed，不降级为不校验）。
    """
    config = load_config(config_path)
    section = _self_update_section(config)
    configured = str(section.get("trusted_public_key") or "").strip()
    key = decode_public_key(configured or config.update_trust_public_key)
    # ★ 更新源默认＝GitHub 官方发布页的「最新 release 资产」固定链接（用户 2026-09-29 拍板）；
    #   显式配置 feed_url 仍可指向镜像/本地目录（镜像不必可信，清单带 sha256）。
    base = str(section.get("feed_url") or "").strip() or DEFAULT_FEED_URL
    return section, config, key, base


def _local_payload_hashes(files: Mapping[str, Any], root: Path) -> dict[str, str]:
    """只对**清单里出现的路径**算本机哈希（不遍历应用根，避免误扫用户数据目录）。

    清单里的路径已由解析层校验过（相对路径、无 ``..``），此处按 posix 语义拼接。
    """
    found: dict[str, str] = {}
    for relative in files:
        path = root.joinpath(*PurePosixPath(relative).parts)
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        found[relative] = digest.hexdigest()
    return found


def _is_remote(base: str) -> bool:
    return base.startswith(("http://", "https://"))


def _fetch_feed(base: str, platform: str, config: AppConfig) -> bytes:
    """取**本平台**的更新清单：先 ``update-<platform>.json``，没有则回退 ``update.json``。

    回退是给"只发一份通用清单"的旧更新源留的兼容路径；两条都取不到时抛原始错误。
    """
    try:
        return _fetch(base, feed_filename(platform), config)
    except Exception:  # noqa: BLE001 - 回退到通用清单名，失败时再抛
        return _fetch(base, FEED_FILENAME, config)


def _require_matching_platform(feed: Any, platform: str) -> None:
    """清单若声明了 ``platform`` ⇒ 必须与**本机平台**一致（fail-closed）。

    否则 Windows 客户端可能拿到 Linux 的清单，进而按它的逐文件清单把 ELF/``.so`` 覆盖进本机。
    """
    declared = str(getattr(feed, "platform", "") or "").strip().lower()
    if declared and declared != platform.strip().lower():
        raise UpdateFeedError(
            f"更新清单声明的平台是 {declared}，与本机 {platform} 不一致 ⇒ 拒绝使用"
        )


def _fetch(base: str, relative: str, config: AppConfig) -> bytes:
    """受控取回：远程基址必须给出 EgressBroker，本地目录不需要（也不出网）。"""
    egress = None
    if _is_remote(base):
        from ..security.egress import EgressBroker

        egress = EgressBroker(config)
    return fetch_resource(base, relative, egress=egress)


def _disabled_payload(reason: str) -> dict[str, Any]:
    return {
        "status": "disabled",
        "detail": (
            f"{reason}：应用自更新已禁用（fail-closed —— 本命令不提供跳过验签或跳过信任根的开关）"
        ),
        "current_version": __version__,
    }


def check(
    *, config_path: str, platform: str = "", edition: str = "", payload_root: Path | None = None
) -> tuple[dict[str, Any], int]:
    """``self-update check``：读更新源 → 验签 → 与已装版本比较（+ 本机载荷比对）。

    ``payload_root`` 只作为测试接缝（默认＝真实应用根）；与"要下哪些文件"的比对
    只读清单里出现的路径，不遍历用户的 ``work``/``data`` 等目录。
    """
    try:
        section, config, key, base = _resolve(config_path)
    except UpdateFeedError as exc:
        return {"status": "failed", "detail": str(exc), "current_version": __version__}, EXIT_FAILED

    if key is None:
        return _disabled_payload("未配置 self_update.trusted_public_key"), EXIT_DISABLED
    if not base:
        return _disabled_payload("未配置 self_update.feed_url"), EXIT_DISABLED

    target_platform = platform or detect_platform()
    try:
        feed = verify_feed_document(
            _fetch_feed(base, target_platform, config), trusted_public_key=key
        )
        _require_matching_platform(feed, target_platform)
    except Exception as exc:  # 网络 / 缺文件 / 验签 / 字段非法 / 平台不符，统一一种可读结论
        return {
            "status": "failed",
            "detail": f"读取或校验更新源失败：{exc}",
            "current_version": __version__,
            "feed_base": base,
        }, EXIT_FAILED

    result = check_feed(
        feed,
        current_version=__version__,
        platform=platform or detect_platform(),
        edition=edition or str(section.get("edition") or DEFAULT_EDITION),
    )

    root = Path(payload_root) if payload_root is not None else application_dir()

    # ★ "忽略此版本的更新"：同一版本不再提示；出现更新的版本自动恢复（本地记录，一个文件）。
    if result.update_available and feed.version == get_ignored_version(root):
        return {
            "status": "ignored",
            "detail": (
                f"已忽略版本 {feed.version} 的更新"
                f"（恢复：omnicrawler self-update ignore --clear）"
            ),
            "current_version": result.current_version,
            "latest_version": result.latest_version,
            "feed_base": base,
        }, EXIT_OK

    detail = result.detail
    payload: dict[str, Any] = {
        "status": result.status,
        "detail": detail,
        "current_version": result.current_version,
        "latest_version": result.latest_version,
        "feed_base": base,
    }

    # ★ 选项与体积（有更新时**总是**给出；增量是否可用取决于"有逐文件清单 ∧ 有对应变更包"）。
    #   体积对比照 B 站弹窗的形态：增量与全量并排，用户一眼看到省了多少。
    delta = (
        feed.payload_delta.get(__version__)
        if (result.update_available and feed.payload_files) else None
    )
    full_asset = (
        feed.asset_for(
            platform or detect_platform(),
            edition or str(section.get("edition") or DEFAULT_EDITION),
        )
        if result.update_available else None
    )
    full_fallback = feed.full_fallback if result.update_available else None
    full_size = (
        full_asset.size if full_asset is not None
        else (full_fallback.size if full_fallback is not None else 0)
    )
    payload["options"] = {
        "incremental": {
            "available": delta is not None,
            "size": delta.size if delta is not None else 0,
        },
        "full": {
            "available": full_asset is not None or full_fallback is not None,
            "size": full_size,
            "name": (
                full_asset.name if full_asset is not None
                else (full_fallback.name if full_fallback is not None else "")
            ),
            "via_fallback": full_asset is None and full_fallback is not None,
        },
        "install_to_versions": full_asset is not None or full_fallback is not None,
        "ignored": False,
    }

    # ★ 本机比对（有逐文件清单时）：把"到底要不要重下 2G"变成一个具体数字。
    #   **不遍历**整个应用根，只读清单里出现的路径——用户数据目录绝不会被扫到。
    if feed.payload_files and result.update_available:
        root = Path(payload_root) if payload_root is not None else application_dir()
        plan = plan_payload(
            feed,
            local_hashes=_local_payload_hashes(feed.payload_files, root),
            # ★ 必须用"文件是否存在"判删除清单：被删的路径本来就不在目标清单里，
            #   拿 local_hashes 去查会恒为假（实测踩过）。
            exists=lambda relative: root.joinpath(*PurePosixPath(relative).parts).is_file(),
        )
        payload["payload_plan"] = {
            "files_total": len(feed.payload_files),
            "unchanged": plan.unchanged,
            "needs_download": plan.needs_download,
            "to_fetch": list(plan.to_fetch),
            "missing_locally": list(plan.missing_locally),
            "fetch_bytes": plan.fetch_bytes,
            "delta_for_current_version": delta.name if delta else "",
            "removed_in_new_version": list(plan.present_deleted),
            "payload_base": feed.payload_base or base,
        }
        full_label = _human_bytes(full_size) if full_size else "本版未提供"
        payload["detail"] = (
            f"{detail}；本机需更新 {plan.needs_download}/{len(feed.payload_files)} 个文件"
            f"（增量约 {_human_bytes(delta.size if delta else plan.fetch_bytes)}"
            f" · 全量包 {full_label}）"
        )

    if result.notes:
        payload["notes"] = result.notes
    if result.asset is not None:
        payload["asset"] = {
            "key": result.asset.key,
            "name": result.asset.name,
            "sha256": result.asset.sha256,
            "size": result.asset.size,
        }
    return payload, (EXIT_UPDATE_AVAILABLE if result.update_available else EXIT_OK)


#: 便携包压缩档里的顶层目录名（`build_windows.ps1` 用 `--root-name 'OmniCrawler'` 打包）。
PORTABLE_ZIP_ROOT = "OmniCrawler"

#: 布局 B 的指针文件：存在 ⇒ 启动器优先跑它指向的版本；不存在 ⇒ 按就地布局启动。
CURRENT_POINTER = "versions/current.txt"

#: 就地布局下需要"退役"的入口（防止用户误点旧版、又触发一次更新）。
_RETIRE_ENTRIES = ("OmniCrawler.exe", "OmniCrawler-Launcher.bat")


def _write_current_pointer(root: Path, version: str) -> Path:
    """写布局 B 的版本指针。**只新增、不移动**：应用根里那份原样保留＝天然回退。"""
    pointer = root / CURRENT_POINTER
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(f"{version.strip()}\n", encoding="utf-8")
    return pointer


def _retire_inplace_entries(root: Path) -> list[str]:
    """把就地布局的旧入口改名 `.outdated`（防误点）。**改名在运行中可行**（实测），
    失败（被占用且不允许改名）则原样保留并如实返回，不静默。"""
    retired: list[str] = []
    for name in _RETIRE_ENTRIES:
        entry = root / name
        if not entry.exists():
            continue
        try:
            entry.rename(root / f"{name}.outdated")
            retired.append(f"{name} -> {name}.outdated")
        except OSError as exc:
            retired.append(f"{name} 未能改名（{exc}）")
    return retired


def _human_bytes(size: int) -> str:
    """人类可读体积（照 B 站弹窗的形态：一位小数）。"""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}GB"


def _ignored_version_file(root: Path) -> Path:
    """本地记录的"忽略此版本"（一个文件，不属于配置 ⇒ 不跨机器同步）。"""
    return root / ".updates" / "ignored-version.txt"


def get_ignored_version(root: Path) -> str:
    """读"忽略此版本"；文件不存在/损坏 ⇒ 空串（绝不因此报错）。"""
    try:
        return _ignored_version_file(root).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def set_ignored_version(root: Path, version: str) -> None:
    path = _ignored_version_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{version.strip()}\n", encoding="utf-8")


def clear_ignored_version(root: Path) -> bool:
    path = _ignored_version_file(root)
    if not path.exists():
        return False
    path.unlink()
    return True


def ignore(
    *, config_path: str, clear: bool = False, app_root: Path | None = None
) -> tuple[dict[str, Any], int]:
    """``self-update ignore``：记录 / 清除"忽略此版本的更新"。

    记录时**必须先读更新源**拿到它当前给出的版本号 —— 不接受用户随手填一个版本，
    避免"忽略了一个根本不存在的版本，结果永远收不到提示"。
    """
    root = Path(app_root) if app_root is not None else application_dir()
    if clear:
        removed = clear_ignored_version(root)
        return {
            "status": "cleared" if removed else "nothing-to-clear",
            "detail": "已恢复提示" if removed else "本来就没有被忽略的版本",
            "current_version": __version__,
        }, EXIT_OK
    try:
        _section, config, key, base = _resolve(config_path)
    except UpdateFeedError as exc:
        return {"status": "failed", "detail": str(exc), "current_version": __version__}, EXIT_FAILED
    if key is None:
        return _disabled_payload("未配置 self_update.trusted_public_key"), EXIT_DISABLED
    target_platform = detect_platform()
    try:
        feed = verify_feed_document(
            _fetch_feed(base, target_platform, config), trusted_public_key=key
        )
        _require_matching_platform(feed, target_platform)
    except Exception as exc:
        return {
            "status": "failed",
            "detail": f"读取或校验更新源失败：{exc}",
            "current_version": __version__,
        }, EXIT_FAILED
    set_ignored_version(root, feed.version)
    return {
        "status": "ignored",
        "detail": (
            f"已忽略版本 {feed.version} 的更新；之后出现更新的版本会自动恢复提示"
            f"（恢复：omnicrawler self-update ignore --clear）"
        ),
        "current_version": __version__,
        "ignored_version": feed.version,
    }, EXIT_OK



def cleanup(
    *, app_root: Path | None = None, yes: bool = False
) -> tuple[dict[str, Any], int]:
    r"""``self-update cleanup``：回收磁盘——清挂账残留 + 删不再使用的旧版本目录。

    **不需要更新源与信任根**（纯清理，不触网、不验签）。两步都做：

    1. 挂账残留（``.updates/pending-cleanup.json``，上次更新时被占用而删不掉的文件）；
    2. ``versions\`` 下不再被 `current.txt` 指向的旧版本目录（就地布局下即"全部"）。

    ``--yes`` 才真正删除；缺省只报计划（与 apply 同一安全判据）。
    """
    root = Path(app_root) if app_root is not None else application_dir()
    manager = UpgradeManager(root, trusted_public_key=None)

    pending_plan = manager.pending_cleanup()
    stale_dirs = manager.stale_version_dirs()
    plan: dict[str, Any] = {
        "app_root": str(root),
        "pending_cleanup": pending_plan,
        "stale_version_dirs": [
            {"path": str(package.relative_to(root)), "size": size}
            for package, size in stale_dirs
        ],
        "freeable_bytes": sum(size for _package, size in stale_dirs),
        "workspace_protected": True,
    }
    if not yes:
        plan["status"] = "dry-run"
        plan["detail"] = "清理计划已生成（加 --yes 才真正删除）"
        return plan, EXIT_OK

    pending_result = manager.run_pending_cleanup()
    versions_result = manager.remove_stale_version_dirs()
    detail = (
        f"已清理挂账残留 {pending_result.get('removed', 0)} 个"
        f"；删除旧版本目录 {len(versions_result.get('removed', []))} 个"
        f"（回收约 {versions_result.get('freed_bytes', 0)} 字节）"
    )
    if pending_result.get("remaining"):
        detail += f"；仍有 {len(pending_result['remaining'])} 个被占用、留待下次"
    return {
        "status": "cleaned",
        "detail": detail,
        "pending_removed": pending_result.get("removed", 0),
        "pending_remaining": pending_result.get("remaining", []),
        "version_dirs_removed": versions_result.get("removed", []),
        "version_dirs_failed": versions_result.get("failed", []),
        "freed_bytes": versions_result.get("freed_bytes", 0),
        "plan": plan,
    }, EXIT_OK


def apply(
    *,
    config_path: str,
    platform: str = "",
    edition: str = "",
    package: str = "",
    dry_run: bool = False,
    app_root: Path | None = None,
    force_full: bool = False,
    full: bool = False,
    to_versions: bool = False,
) -> tuple[dict[str, Any], int]:
    """``self-update apply``：落地更新（**优先只下变化的那部分**）。

    三条路径，按优先级：
    1. **增量**（默认优先）：更新源为该版本提供了**变更包**（``payload.delta[当前版本]``）
       ⇒ 只下这个包（几 MB），逐成员对已签名清单核哈希后**就地替换**；
    2. **离线包**（``package`` 非空）：用一个本地已签名升级包（整包）；
    3. **整包**（``use force_full`` 或缺变更包）：按 ``assets`` 下整包再替换。

    ``app_root`` / ``force_full`` 只作为测试接缝与显式降级入口，**不作为 CLI 开关**，
    避免把"替换到哪儿"变成可由参数指定的行为。

    ★ 每次真正落地前都会先执行**上次挂账的待清理**（"下次启动清理"的落点）；
    ``--dry-run`` 既不清理也不写盘。
    """
    try:
        section, config, key, base = _resolve(config_path)
    except UpdateFeedError as exc:
        return {"status": "failed", "detail": str(exc), "current_version": __version__}, EXIT_FAILED

    if key is None:
        return _disabled_payload("未配置 self_update.trusted_public_key"), EXIT_DISABLED

    root = Path(app_root) if app_root is not None else application_dir()
    manager = UpgradeManager(root, trusted_public_key=key)
    local_package = Path(package).expanduser() if package else None

    feed: Any = None
    asset: Any = None
    fallback_used: FullFallback | None = None
    fetch_base = ""
    target_name = ""
    expected_sha = ""
    if local_package is None:
        target_platform = platform or detect_platform()
        try:
            feed = verify_feed_document(
                _fetch_feed(base, target_platform, config), trusted_public_key=key
            )
            _require_matching_platform(feed, target_platform)
        except Exception as exc:
            return {
                "status": "failed",
                "detail": f"读取或校验更新源失败：{exc}",
                "current_version": __version__,
            }, EXIT_FAILED
        asset = feed.asset_for(
            platform or detect_platform(),
            edition or str(section.get("edition") or DEFAULT_EDITION),
        )
        if asset is None:
            # 本平台没有全量包（小版本不重建全量）⇒ 用「最近一次带全量包的发布」兜底，
            # 否则不在增量基线内的用户会被永久卡住。
            fallback_used = feed.full_fallback
            if fallback_used is None:
                wanted = asset_key(
                    platform or detect_platform(),
                    edition or str(section.get("edition") or DEFAULT_EDITION),
                )
                return {
                    "status": "failed",
                    "detail": (
                        f"更新源未提供本平台资产（{wanted}），且清单没有 full_fallback 兜底"
                    ),
                    "current_version": __version__,
                }, EXIT_FAILED
            target_name, expected_sha = fallback_used.name, fallback_used.sha256
            fetch_base = fallback_used.base
        else:
            target_name, expected_sha = asset.name, asset.sha256
            fetch_base = base

    # ★ 增量优先：更新源若提供"相对当前版本的变更包"，就只下它（几 MB），
    #   只替换**本机确实不同**的那些文件 —— 这是"不会真的下 2G"的落点。
    delta = (
        feed.payload_delta.get(__version__)
        if (feed is not None and not force_full and not full and not to_versions)
        else None
    )
    delta_expected: dict[str, str] = {}
    if delta is not None and feed.payload_files:
        local = _local_payload_hashes(feed.payload_files, root)
        delta_plan = plan_payload(
            feed,
            local_hashes=local,
            exists=lambda relative: root.joinpath(*PurePosixPath(relative).parts).is_file(),
        )
        delta_expected = {
            relative: feed.payload_files[relative].sha256
            for relative in (*delta_plan.to_fetch, *delta_plan.missing_locally)
            if relative in feed.payload_files
        }
        if not delta_expected:
            delta = None  # 本机已与目标一致：一个成员都不用下

    # ★ `--to-versions` 的**消费者只有 Windows 启动器**（`OmniCrawler-Launcher.bat` 读
    #   `versions\current.txt`）；Linux/macOS 上没有任何东西会读那个指针 ⇒ 静默产出
    #   "装了但没人启动它"的布局比拒绝更糟。这里明确拒绝并指路（Linux 侧包一层解析脚本
    #   属后续项，需与 install-user.sh / 桌面入口一起改）。
    if to_versions:
        target_platform = platform or detect_platform()
        if target_platform != "windows":
            return {
                "status": "failed",
                "detail": (
                    f"--to-versions 目前仅 Windows 可用（当前平台 {target_platform}）："
                    "该布局靠启动器读取 versions/current.txt，而 Linux/macOS 暂无消费者。"
                    "请改用 --full（全量·就地替换）。"
                ),
                "current_version": __version__,
            }, EXIT_FAILED

    if delta is not None:
        mode = "incremental"
    elif local_package is not None:
        mode = "offline-package"
    elif to_versions:
        mode = "versions-install"
    else:
        # ★ 全量兜底：显式 --full，或本机版本不在增量基线内（跨大版本）⇒ 只能全量，否则永久卡住
        mode = "full-archive"
    plan: dict[str, Any] = {
        "app_root": str(root),
        "mode": mode,
        "package": (
            delta.name if delta is not None else (str(local_package) if local_package else target_name)
        ),
        "sha256": (
            delta.sha256 if delta is not None
            else (expected_sha or "(离线包：仅按包内 upgrade.json 验签 + 逐文件哈希)")
        ),
        "download_bytes": (
            delta.size if delta is not None
            else ((asset.size if asset is not None else 0) or 0)
        ),
        "full_fallback": (
            {"version": fallback_used.version, "name": fallback_used.name}
            if fallback_used is not None else None
        ),
        "files_to_replace": len(delta_expected) if delta is not None else 0,
        "workspace_protected": True,
    }
    if dry_run:
        plan["status"] = "dry-run"
        plan["detail"] = "计划已生成，未写入任何文件（去掉 --dry-run 并加 --yes 才执行）"
        return plan, EXIT_OK

    # ★ "下次启动清理"的落点：先把上次挂账的残留收掉（幂等、尽力而为、不阻塞本次更新）
    cleanup = manager.run_pending_cleanup()
    incoming = root / ".updates" / "incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    deleted_removed = 0
    failed_removed: list[str] = []
    try:
        staged_version = ""
        if delta is not None:
            raw = _fetch(feed.payload_base or base, delta.name, config)
            actual = hashlib.sha256(raw).hexdigest()
            if actual != delta.sha256:
                return {
                    "status": "failed",
                    "detail": (
                        f"变更包 sha256 与更新源不一致（期望 {delta.sha256}，实际 {actual}）——已拒绝应用"
                    ),
                    "current_version": __version__,
                    "plan": plan,
                }, EXIT_FAILED
            delta_path = incoming / Path(delta.name).name
            delta_path.write_bytes(raw)
            # 成员逐个对**已签名清单**里的哈希核对（变更包自身无需签名）
            staged = manager.stage_members(delta_path, delta_expected, version=feed.version)
            staged_version = str(staged.get("version") or "")
            applied = manager.apply(Path(str(staged["stage"])))
        elif local_package is None:
            # 全量：整包（便携包 zip）。整包哈希来自**已签名清单** ⇒ 先核包再落地，
            # 一个字节不符就一个文件都不写。
            raw = _fetch(fetch_base, target_name, config)
            actual = hashlib.sha256(raw).hexdigest()
            if actual != expected_sha:
                return {
                    "status": "failed",
                    "detail": (
                        f"下载包 sha256 与更新源不一致（期望 {expected_sha}，实际 {actual}）——已拒绝应用"
                    ),
                    "current_version": __version__,
                    "plan": plan,
                }, EXIT_FAILED
            archive_path = incoming / Path(target_name).name
            archive_path.write_bytes(raw)
            staged_version = feed.version if feed is not None else ""
            if to_versions:
                # 大版本可选：装到 versions/<新版>/，应用根那份**原样不动**＝天然回退
                versions_root = root / "versions" / staged_version
                applied = manager.apply_archive(
                    archive_path,
                    strip_root=PORTABLE_ZIP_ROOT,
                    expected_sha256=expected_sha,
                    dest_root=versions_root,
                )
                _write_current_pointer(root, staged_version)
                plan["retired_entries"] = _retire_inplace_entries(root)
            else:
                # 全量·就地替换：只占一份（--full 显式选择，或本机不在增量基线内的兜底）
                applied = manager.apply_archive(
                    archive_path, strip_root=PORTABLE_ZIP_ROOT, expected_sha256=expected_sha
                )
        else:
            if not local_package.is_file():
                return {
                    "status": "failed",
                    "detail": f"本地升级包不存在：{local_package}",
                    "current_version": __version__,
                }, EXIT_FAILED
            staged = manager.stage(local_package)
            staged_version = str(staged.get("version") or "")
            applied = manager.apply(Path(str(staged["stage"])))
        if delta is not None:
            deleted_removed, failed_removed = _remove_deleted(feed, root)
    except Exception as exc:
        return {
            "status": "failed",
            "detail": f"应用更新失败（已尽力回滚：新文件删除、旧文件改名还原）：{exc}",
            "current_version": __version__,
            "plan": plan,
        }, EXIT_FAILED

    pending: list[str] = [str(item) for item in (applied.get("pending_cleanup") or [])]
    if failed_removed:
        manager.record_pending(failed_removed)
        pending.extend(failed_removed)
    detail = (
        f"已就地替换 {applied.get('applied')} 个文件（{mode}）；工作区数据未改动；"
        f"旧文件当场删除 {applied.get('removed')} 个"
    )
    if pending:
        detail += f"，另 {len(pending)} 个被占用、已登记到下次启动清理"
    if cleanup["removed"]:
        detail += f"；本次顺带清掉了上次挂账的 {cleanup['removed']} 个残留"
    return {
        "status": "applied",
        "detail": detail,
        "previous_version": __version__,
        "staged_version": staged_version,
        "applied_files": applied.get("applied"),
        "pending_cleanup": pending,
        "deleted_removed": deleted_removed,
        "cleaned_previous_pending": cleanup["removed"],
        "plan": plan,
    }, EXIT_OK


def _remove_deleted(feed: Any, root: Path) -> tuple[int, list[str]]:
    """处理清单里的"本版已移除"路径，返回 ``(删除成功数, 删不掉的残留)``。

    删不掉（被占用）的交给调用方挂账到待清理清单 —— 不做成静默忽略：残留文件属于
    "下一版已经不要、但本机还留着"的隐患，必须可见可追。
    """
    removed = 0
    failed: list[str] = []
    for relative in feed.payload_deleted:
        path = root.joinpath(*PurePosixPath(relative).parts)
        if not path.exists():
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            failed.append(str(relative))
    return removed, failed
