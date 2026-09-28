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
from pathlib import Path
from typing import Any

from .._version import __version__
from ..core.config import AppConfig, load_config
from ..core.runtime_paths import application_dir
from ..plugins.market_client import fetch_resource
from ..services.update_feed import (
    DEFAULT_EDITION,
    FEED_FILENAME,
    UpdateFeedError,
    asset_key,
    check_feed,
    decode_public_key,
    detect_platform,
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
    """读配置并解出（段, config, 信任根, 更新源基址）。信任根非法即抛错。"""
    config = load_config(config_path)
    section = _self_update_section(config)
    key = decode_public_key(str(section.get("trusted_public_key") or ""))
    return section, config, key, str(section.get("feed_url") or "").strip()


def _is_remote(base: str) -> bool:
    return base.startswith(("http://", "https://"))


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
    *, config_path: str, platform: str = "", edition: str = ""
) -> tuple[dict[str, Any], int]:
    """``self-update check``：读更新源 → 验签 → 与已装版本比较。"""
    try:
        section, config, key, base = _resolve(config_path)
    except UpdateFeedError as exc:
        return {"status": "failed", "detail": str(exc), "current_version": __version__}, EXIT_FAILED

    if key is None:
        return _disabled_payload("未配置 self_update.trusted_public_key"), EXIT_DISABLED
    if not base:
        return _disabled_payload("未配置 self_update.feed_url"), EXIT_DISABLED

    try:
        feed = verify_feed_document(
            _fetch(base, FEED_FILENAME, config), trusted_public_key=key
        )
    except Exception as exc:  # 网络 / 缺文件 / 验签 / 字段非法，统一一种可读结论
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
    payload: dict[str, Any] = {
        "status": result.status,
        "detail": result.detail,
        "current_version": result.current_version,
        "latest_version": result.latest_version,
        "feed_base": base,
    }
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


def apply(
    *,
    config_path: str,
    platform: str = "",
    edition: str = "",
    package: str = "",
    dry_run: bool = False,
    app_root: Path | None = None,
) -> tuple[dict[str, Any], int]:
    """``self-update apply``：取包 → 核对 sha256 → 验签 stage → 覆盖 apply（可回滚）。

    ``package`` 非空时走**离线路径**（不读更新源，直接用本地已签名包）。
    ``app_root`` 只作为测试接缝（默认＝真实应用根）；**不作为 CLI 开关**，
    避免把"覆盖到哪儿"变成可由参数指定的行为。
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

    target_name = ""
    expected_sha = ""
    if local_package is None:
        if not base:
            return _disabled_payload("未配置 self_update.feed_url"), EXIT_DISABLED
        try:
            feed = verify_feed_document(
                _fetch(base, FEED_FILENAME, config), trusted_public_key=key
            )
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
            wanted = asset_key(
                platform or detect_platform(),
                edition or str(section.get("edition") or DEFAULT_EDITION),
            )
            return {
                "status": "failed",
                "detail": (f"更新源未提供本平台资产（{wanted}）"),
                "current_version": __version__,
            }, EXIT_FAILED
        target_name, expected_sha = asset.name, asset.sha256

    plan: dict[str, Any] = {
        "app_root": str(root),
        "package": str(local_package) if local_package else target_name,
        "sha256": expected_sha or "(离线包：仅按包内 upgrade.json 验签 + 逐文件哈希)",
        "workspace_protected": True,
    }
    if dry_run:
        plan["status"] = "dry-run"
        plan["detail"] = "计划已生成，未写入任何文件（去掉 --dry-run 并加 --yes 才执行）"
        return plan, EXIT_OK

    incoming = root / ".updates" / "incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    try:
        if local_package is None:
            raw = _fetch(base, target_name, config)
            actual = hashlib.sha256(raw).hexdigest()
            if actual != expected_sha:
                return {
                    "status": "failed",
                    "detail": (
                        f"下载包 sha256 与更新源不一致（期望 {expected_sha}，实际 {actual}）——已拒绝应用"
                    ),
                    "current_version": __version__,
                }, EXIT_FAILED
            local_package = incoming / Path(target_name).name
            local_package.write_bytes(raw)
        elif not local_package.is_file():
            return {
                "status": "failed",
                "detail": f"本地升级包不存在：{local_package}",
                "current_version": __version__,
            }, EXIT_FAILED

        staged = manager.stage(local_package)
        applied = manager.apply(Path(str(staged["stage"])))
    except Exception as exc:
        return {
            "status": "failed",
            "detail": f"应用升级包失败（已按需回滚）：{exc}",
            "current_version": __version__,
            "plan": plan,
        }, EXIT_FAILED

    return {
        "status": "applied",
        "detail": (
            f"已从 {staged.get('version')} 升级包应用 {applied.get('applied')} 个文件；"
            "工作区数据未改动；可用 rollback 目录回退"
        ),
        "previous_version": __version__,
        "staged_version": staged.get("version"),
        "applied_files": applied.get("applied"),
        "rollback_dir": applied.get("rollback"),
        "plan": plan,
    }, EXIT_OK
