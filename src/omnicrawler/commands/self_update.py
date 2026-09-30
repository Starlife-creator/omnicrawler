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
import zipfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from .._version import __version__
from ..core.config import AppConfig, load_config
from ..core.runtime_paths import VERSIONS_DIRNAME, application_dir, install_root
from ..plugins.market_client import fetch_resource
from ..services.update_feed import (
    DEFAULT_EDITION,
    DEFAULT_FEED_URL,
    FEED_FILENAME,
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


def _fetch_feed(base: str, platform: str, edition: str, config: AppConfig) -> tuple[bytes, str]:
    """取**本平台本版本**的更新清单，按精度逐级回退；返回 (文档字节, 实际命中的文件名)。

    顺序：``update-<platform>-<edition>.json`` → ``update-<platform>.json`` → ``update.json``。
    为什么会分三层：载荷在 **平台**（Windows 是 .exe/.dll，Linux 是 ELF/.so）与 **版本**
    （Standard/Full 的 runtime/OCR 差异）两个维度都不同，所以清单必须按 (平台, 版本) 分；
    后两层是给"只发更粗粒度清单"的更新源留的兼容路径。都取不到时抛最后那次错误。

    命中文件名要**回传**：出问题时「用了哪份清单」是首要诊断信息，也让「确实按版本取到了」
    这件事可以被断言（否则只能靠「没报错」间接推断）。
    """
    candidates = [feed_filename(platform, edition), feed_filename(platform), FEED_FILENAME]
    last_error: Exception | None = None
    for index, name in enumerate(candidates):
        if index and candidates[index - 1] == name:
            continue
        try:
            return _fetch(base, name, config), name
        except Exception as exc:  # noqa: BLE001 - 逐级回退，全失败时抛最后一次
            last_error = exc
    assert last_error is not None
    raise last_error


def _require_matching_platform(feed: Any, platform: str) -> None:
    """清单若声明了 ``platform`` ⇒ 必须与**本机平台**一致（fail-closed）。

    否则 Windows 客户端可能拿到 Linux 的清单，进而按它的逐文件清单把 ELF/``.so`` 覆盖进本机。
    """
    declared = str(getattr(feed, "platform", "") or "").strip().lower()
    if declared and declared != platform.strip().lower():
        raise UpdateFeedError(
            f"更新清单声明的平台是 {declared}，与本机 {platform} 不一致 ⇒ 拒绝使用"
        )


def _require_matching_edition(feed: Any, edition: str) -> None:
    """清单若声明了 ``edition`` ⇒ 必须与本机版本一致（与平台同一类 fail-closed 判据）。

    为什么必需：Standard 与 Full 的载荷不同（``runtime/``、OCR 组件），逐文件清单因此不同。
    若把 Full 的清单套在 Standard 安装上，客户端会按"应该有 PaddleOCR"去比对，结果是
    **要么白下几百 MB、要么把不该有的文件当成待落地**；反过来还会漏掉本版该有的文件。
    ``edition`` 缺省为空 ⇒ 不校验（兼容"只发一份粗粒度清单"的老更新源）。
    """
    declared = str(getattr(feed, "edition", "") or "").strip().lower()
    if declared and declared != edition.strip().lower():
        raise UpdateFeedError(
            f"更新清单声明的版本是 {declared}，与本机 {edition} 不一致 ⇒ 拒绝使用"
        )


def _require_matching_target(feed: Any, platform: str, edition: str) -> None:
    """清单声明的 (平台, 版本) 必须与本机一致 —— 两维都查，缺一不可。"""
    _require_matching_platform(feed, platform)
    _require_matching_edition(feed, edition)


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
    # ★ 清单文件名按 (平台, 版本) 定位 ⇒ 版本要与后面选资产时用的**同一个**（Standard/Full
    #   的 runtime/OCR 载荷不同，拿错清单会把另一版的文件清单当成自己的）。
    target_edition = edition or str(section.get("edition") or DEFAULT_EDITION)
    try:
        raw_feed, feed_name = _fetch_feed(base, target_platform, target_edition, config)
        feed = verify_feed_document(raw_feed, trusted_public_key=key)
        _require_matching_target(feed, target_platform, target_edition)
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
        platform=target_platform,
        edition=target_edition,
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
            "feed_document": feed_name,
        }, EXIT_OK

    detail = result.detail
    payload: dict[str, Any] = {
        "status": result.status,
        "detail": detail,
        "current_version": result.current_version,
        "latest_version": result.latest_version,
        "feed_base": base,
        "feed_document": feed_name,
    }

    # ★ 选项与体积（有更新时**总是**给出；增量是否可用取决于"有逐文件清单 ∧ 有对应变更包"）。
    #   体积对比照 B 站弹窗的形态：增量与全量并排，用户一眼看到省了多少。
    delta = (
        feed.payload_delta.get(__version__)
        if (result.update_available and feed.payload_files) else None
    )
    full_asset = feed.asset_for(target_platform, target_edition) if result.update_available else None
    # ★ 能力声明进 options：GUI 与调用方据此把"能自动更新"和"只能手动安装"区分开。
    #   macOS 不在 `AUTO_APPLY_PLATFORMS` 里（主产物是 dmg、`browsers/` 在 .app 之外、
    #   ad-hoc 签名会被改坏）⇒ 如实说"只能手动装"，而不是让用户点一个注定失败的按钮。
    manual_only = result.update_available and not feed.auto_apply
    payload["options"] = {
        "auto_apply": result.update_available and feed.auto_apply,
        "manual_install": manual_only,
        "incremental": {
            "available": delta is not None,
            "size": delta.size if delta is not None else 0,
        },
        "full": {
            "available": full_asset is not None,
            "size": full_asset.size if full_asset is not None else 0,
            "name": full_asset.name if full_asset is not None else "",
        },
        "install_to_versions": full_asset is not None and feed.auto_apply,
        "ignored": False,
    }
    if manual_only:
        where = (
            f"（{full_asset.name}，{_human_bytes(full_asset.size)}）"
            if full_asset is not None
            else f"（本平台资产 {asset_key(target_platform, target_edition)}）"
        )
        payload["detail"] = (
            f"{detail}；★ 本平台**不支持自动更新**，请手动下载并替换"
            f"{where} —— 取自本 Release 的下载页（feed 基址：{base}）"
        )

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
        full_label = _human_bytes(full_asset.size) if full_asset is not None else "本版未提供"
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
#:
#: ★★ **绝不退役启动器**（`OmniCrawler-Launcher.bat` / `*-launcher`）：启动器是
#: **版本无关**的 —— 它读 `versions/current.txt` 再决定启动哪一份。把它改名 `.outdated`
#: 等于把"唯一能启动应用的入口"藏起来（此前 Windows 那份就在名单里，属真缺陷）。
#: 退役只针对**具体版本的那几个二进制**。
_RETIRE_ENTRIES_WINDOWS = ("OmniCrawler.exe",)
_RETIRE_ENTRIES_POSIX = ("OmniCrawler", "omnicrawler")


def _retire_names(platform: str) -> tuple[str, ...]:
    """**目标平台**该退役的就地入口二进制（**不含启动器**）。

    判据取"正在安装的那个平台"而不是 `detect_platform()`：两者在生产环境里必然一致
    （退役动作作用在正在运行的那棵树上），但取显式参数才能在测试里确定性地说清
    "给 linux 装的时候退役哪几个" —— 否则用例会随"跑在哪个系统上"而变。
    """
    if platform.strip().lower() == "windows":
        return _RETIRE_ENTRIES_WINDOWS
    return _RETIRE_ENTRIES_POSIX


def _write_current_pointer(root: Path, version: str) -> Path:
    """写布局 B 的版本指针。**只新增、不移动**：应用根里那份原样保留＝天然回退。"""
    pointer = root / CURRENT_POINTER
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(f"{version.strip()}\n", encoding="utf-8")
    return pointer


def _retire_inplace_entries(root: Path, *, platform: str) -> list[str]:
    """把就地布局的旧入口改名 `.outdated`（防误点）。**改名在运行中可行**（实测），
    失败（被占用且不允许改名）则原样保留并如实返回，不静默。

    ★ **不碰启动器**（见 `_retire_names` 的注释）：它是版本无关的入口，退役掉用户就没法启动了。
    ★ 启动器本身还会做"若 `current.txt` 指向别的版本 ⇒ 转交"的检查，所以即便用户
    直接点了旧二进制，也会被引到当前生效的那份。
    """
    retired: list[str] = []
    for name in _retire_names(platform):
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


class _ApplyHardFailError(RuntimeError):
    """落地过程中产生的「明确失败」（带可读 detail）。

    与外层 `except Exception` 分开：那条路会写"已尽力回滚"，而这些失败**发生在写盘之前**
    （取包、核哈希、比成员），报"已回滚"会误导用户去找一个不存在的半成品状态。
    """


def _delta_missing_members(delta_path: Path, expected: Mapping[str, str]) -> list[str]:
    """变更包里**缺失**的所需成员（相对路径，已排序）。

    为什么需要它（2026-09-30 发现的缺陷）：`plan_payload` 把「本机没有的文件」一律算进
    `missing_locally` —— **包括那些在本机版本与目标版本之间根本没变过的文件**（用户自己删过、
    或被杀软隔离过、或上次更新中断留下缺口）。而变更包只装"相对基线**变化过**的成员"，
    于是 `stage_members` 会抛「变更包缺少清单里的文件」⇒ **整次更新失败且不自愈**：
    用户删过一个文件之后，再也做不了增量更新。

    调用点在**已核对包 sha256 之后** ⇒ 这里读的是一份**已认证**的包，不是来路不明的输入。
    """
    with zipfile.ZipFile(delta_path) as archive:
        present = set(archive.namelist())
    return sorted(relative for relative in expected if relative not in present)


def _layout_root(*, app_root: Path | None, root: Path) -> Path:
    """布局根：`versions/` 与 `current.txt` 所在的那一层。

    默认是 **`install_root()`（安装根）** —— 版本化布局下它 ≠ 运行目录，这是必须分开的原因
    （见 `runtime_paths.install_root`）。但调用方**显式**给了 `app_root` 时（测试接缝 /
    离线自建更新源），那棵被指定的树就是"这份安装"，布局根跟着它；否则测试会写到真实安装目录去。

    ★ 判据落在"调用方有没有显式指定"上，而不是"看起来像不像安装根"：后者会变成"看着像"判据。
    """
    return root if app_root is not None else install_root()


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
    target_edition = str(_section.get("edition") or DEFAULT_EDITION)
    try:
        raw_feed, _feed_name = _fetch_feed(base, target_platform, target_edition, config)
        feed = verify_feed_document(raw_feed, trusted_public_key=key)
        _require_matching_target(feed, target_platform, target_edition)
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
    manager = UpgradeManager(
        root, trusted_public_key=None, install_root=_layout_root(app_root=app_root, root=root)
    )

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
    manager = UpgradeManager(
        root, trusted_public_key=key, install_root=_layout_root(app_root=app_root, root=root)
    )
    local_package = Path(package).expanduser() if package else None

    feed: Any = None
    asset: Any = None
    fetch_base = ""
    target_name = ""
    expected_sha = ""
    # 命中的清单文件名（离线包路径下没有清单 ⇒ 留空）。回传给用户便于回答「用的哪份清单」。
    feed_name = ""
    if local_package is None:
        target_platform = platform or detect_platform()
        target_edition = edition or str(section.get("edition") or DEFAULT_EDITION)
        try:
            raw_feed, feed_name = _fetch_feed(base, target_platform, target_edition, config)
            feed = verify_feed_document(raw_feed, trusted_public_key=key)
            _require_matching_target(feed, target_platform, target_edition)
        except Exception as exc:
            return {
                "status": "failed",
                "detail": f"读取或校验更新源失败：{exc}",
                "current_version": __version__,
            }, EXIT_FAILED
        asset = feed.asset_for(target_platform, target_edition)
        if not feed.auto_apply:
            # ★ 能力声明说"本平台只能手动装" ⇒ **明确拒绝并指路**，不去尝试一个注定失败的落地。
            #   判据来自清单的 `auto_apply`（与 `AUTO_APPLY_PLATFORMS` 同源），而不是客户端
            #   自己按平台名猜 —— 这样"支持面"只有一处真源。
            target_asset = feed.asset_for(target_platform, target_edition)
            what = (
                f"{target_asset.name}（{_human_bytes(target_asset.size)}）"
                if target_asset is not None
                else asset_key(target_platform, target_edition)
            )
            return {
                "status": "failed",
                "detail": (
                    f"本平台不支持自动更新（{target_platform}）：原因见平台能力声明。"
                    f"请手动下载并替换 {what}（本 Release 的下载页：{base}）。"
                ),
                "current_version": __version__,
                "latest_version": feed.version,
                "feed_document": feed_name,
                "manual_install": True,
            }, EXIT_FAILED
        if asset is None:
            # ★ 明确缺失 + 指引（**不再**指向老版本兜底）。
            #   曾经的做法是用 `full_fallback` 指向"最近一次带全量包的发布"，但那是
            #   **把应用静默降级成旧版**却把版本号报成新版（还写进 current.txt）——
            #   错的状态比没有自动路径更糟。缺键只可能是发布侧出错（或该包超 2 GiB 被剔除），
            #   如实说出来并给两条已文档化的替代路径。
            wanted = asset_key(target_platform, target_edition)
            available = ", ".join(sorted(feed.assets)) or "无"
            return {
                "status": "failed",
                "detail": (
                    f"本版未提供本平台本版本的完整包（缺 {wanted}；清单里有：{available}）。"
                    "若它超了 2 GiB 未随发布：本平台本版本只能走增量；"
                    "本机版本不在增量窗口内时，请手动下载某个已发布的完整包重装，"
                    f"或改用 Standard 包 + `omnicrawler components import` 加装组件。"
                ),
                "current_version": __version__,
                "latest_version": feed.version,
                "feed_document": feed_name,
            }, EXIT_FAILED
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

    # ★ `--to-versions`（"保留旧版本"）此前**只在 Windows 可用**并被明确拒绝：Linux/macOS
    #   的入口是安装期烘死的绝对路径（`.desktop` 的 Exec、`~/.local/bin` 软链），都指向
    #   **就地**那一份 ⇒ 装了新版没人启动它。现在三平台都有了读 `versions/current.txt` 的
    #   启动器（Linux：随包发 `OmniCrawler-launcher` / `omnicrawler-cli-launcher`；
    #   Windows：`OmniCrawler-Launcher.bat`），且**指针格式完全一致** ⇒ 拒绝可以撤了。
    #   ★ macOS 仍然只有 `assets`-only 清单、主产物是 dmg（`apply_archive` 只认 zip），
    #   其"能不能落地"是另一件事，不在这里假装解决 —— 见发布侧的 macOS 能力声明。

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
        # 布局（versions/ 与 current.txt）在**安装根**下；就地载荷落在运行目录。
        # 两者在版本化布局下不同 ⇒ 两个都报出来，出问题时一眼看得出用的哪个。
        "install_root": str(manager.install_root),
        "mode": mode,
        "feed_document": feed_name,
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
        "files_to_replace": len(delta_expected) if delta is not None else 0,
        "workspace_protected": True,
    }

    def _apply_full_archive() -> tuple[str, dict[str, Any]]:
        """全量：整包 zip。整包哈希来自**已签名清单** ⇒ 先核包再落地，一个字节不符一个文件都不写。

        抽成函数是为了让"增量包覆盖不到本机状态"时能**原地降级重试**（见下面的自愈分支）。
        """
        raw = _fetch(fetch_base, target_name, config)
        actual = hashlib.sha256(raw).hexdigest()
        if actual != expected_sha:
            raise _ApplyHardFailError(
                f"下载包 sha256 与更新源不一致（期望 {expected_sha}，实际 {actual}）——已拒绝应用"
            )
        archive_path = incoming / Path(target_name).name
        archive_path.write_bytes(raw)
        version = feed.version if feed is not None else ""
        if to_versions:
            # 可选：装到 <安装根>/versions/<新版>/，就地那份**原样不动**＝天然回退。
            # ★ 目录与指针都放**安装根**（不是运行目录）：版本化布局下运行目录是
            #   `<安装根>/versions/<v>/`，照旧写就会套出 `<v>/versions/<新版>/`（实测可复现）。
            versions_root = manager.install_root / VERSIONS_DIRNAME / version
            result = manager.apply_archive(
                archive_path,
                strip_root=PORTABLE_ZIP_ROOT,
                expected_sha256=expected_sha,
                dest_root=versions_root,
            )
            _write_current_pointer(manager.install_root, version)
            plan["retired_entries"] = _retire_inplace_entries(root, platform=target_platform)
            plan["versions_root"] = str(versions_root)
        else:
            # 全量·就地替换：只占一份（--full 显式选择，或本机不在增量基线内的兜底）
            result = manager.apply_archive(
                archive_path, strip_root=PORTABLE_ZIP_ROOT, expected_sha256=expected_sha
            )
        return version, result

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
                raise _ApplyHardFailError(
                    f"变更包 sha256 与更新源不一致（期望 {delta.sha256}，实际 {actual}）——已拒绝应用"
                )
            delta_path = incoming / Path(delta.name).name
            delta_path.write_bytes(raw)
            # ★★ 自愈：变更包只装"相对基线**变化过**"的成员，而本机可能有"两版之间没变过、
            #   但本机被删掉/改坏"的文件 —— 那些成员**不在包里**。此前会直接抛"变更包缺少清单里
            #   的文件"⇒ 整次更新失败且不自愈（用户删过一个文件就再也做不了增量更新）。
            #   正解：不硬失败，改为**降级走全量**，并把缺失路径如实报出来（可见、不静默）。
            missing_members = _delta_missing_members(delta_path, delta_expected)
            if missing_members:
                plan["delta_fallback"] = {
                    "package": delta.name,
                    "missing_count": len(missing_members),
                    "missing_members": missing_members[:20],
                    "reason": "本机有「变更包未包含」的所需文件（多为本地自行改动或上次更新中断）⇒ 改用全量",
                }
                delta = None
                delta_expected = {}
                mode = "versions-install" if to_versions else "full-archive"
                plan["mode"] = mode
                plan["files_to_replace"] = 0
                plan["package"] = target_name
                plan["sha256"] = expected_sha
                plan["download_bytes"] = (asset.size if asset is not None else 0) or 0
        if delta is not None:
            # 成员逐个对**已签名清单**里的哈希核对（变更包自身无需签名）
            staged = manager.stage_members(delta_path, delta_expected, version=feed.version)
            staged_version = str(staged.get("version") or "")
            applied = manager.apply(Path(str(staged["stage"])))
        elif local_package is None:
            staged_version, applied = _apply_full_archive()
        else:
            if not local_package.is_file():
                raise _ApplyHardFailError(f"本地升级包不存在：{local_package}")
            staged = manager.stage(local_package)
            staged_version = str(staged.get("version") or "")
            applied = manager.apply(Path(str(staged["stage"])))
        if feed is not None:
            # 删除清单来自**已签名清单**：变更包里刻意不带删除语义（包是不可信容器）
            deleted_removed, failed_removed = _remove_deleted(feed, root)
    except _ApplyHardFailError as exc:
        return {
            "status": "failed",
            "detail": str(exc),
            "current_version": __version__,
            "plan": plan,
        }, EXIT_FAILED
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
