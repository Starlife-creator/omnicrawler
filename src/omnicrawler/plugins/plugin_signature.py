"""插件签名策略常量与「签名验签门」。

由 ``plugins.py`` 抽出的姊妹模块（P1-3 拆分）。依赖方向：``plugins.py`` →
``plugin_signature`` → ``plugin_trust_prompt``（叶子），**无循环导入**。

职责：
- :data:`SIGNATURE_POLICY_STRICT` / :data:`SIGNATURE_POLICY_DEVELOPER` / :data:`SIGNATURE_POLICIES`
  —— 签名策略取值（从 ``plugins.py`` 迁入，语义上属于验签域）；
- :func:`_resolve_trust_root` —— 信任根解析（内联 PEM 或根目录相对路径）；
- :func:`_verify_plugin_signature` —— 按来源分级 + 签名策略的验签门。

``plugins.py`` 以同名再导出保持既有导入路径 ``omnicrawler.plugins.plugins.X`` 可用。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..core.config import AppConfig
from . import signing
from .plugin_trust_prompt import TrustPrompter, TrustPromptResult

LOGGER = logging.getLogger(__name__)

SIGNATURE_POLICY_STRICT = "strict"
SIGNATURE_POLICY_DEVELOPER = "developer"
SIGNATURE_POLICIES = (SIGNATURE_POLICY_STRICT, SIGNATURE_POLICY_DEVELOPER)


def _resolve_trust_root(config: AppConfig | None, trust_source: str) -> str:
    """Resolve a trust root given as inline PEM or a (root-relative) path."""

    if "-----BEGIN" in trust_source:
        return trust_source
    candidate = Path(trust_source)
    if not candidate.is_absolute() and config is not None:
        candidate = config.resolve(trust_source)
    if candidate.is_file():
        return candidate.read_text(encoding="utf-8").strip()
    return trust_source


def _verify_plugin_signature(
    path: Path,
    config: AppConfig | None,
    *,
    signature_policy: str,
    trust_prompter: TrustPrompter | None,
    plugin_bytes: bytes | None = None,
    is_market: bool = False,
) -> Any:
    """签名验签门（按来源分级 + 签名策略）。

    分层：
    - 维护者签名（``plugin.py.sig``；旧版 ``maintainer.sig`` 已弃用，验证器不再兼容）经信任根验证 → 自动信任；
    - 创作者签名（``creator.sig`` + ``creator.identity``）指纹在本地信任列表 → 信任；
    - 创作者签名有效但未在信任列表：strict 策略下经信任询问器确认
      （信任并加载 / 仅本次加载 / 拒绝）；developer 策略下警告放行；
    - 无有效签名：strict 一律拒载；developer 策略下警告放行（开发模式）。

    ``plugin_bytes`` 传入调用方已读好的内容，验签基于**同一份字节**（TOCTOU 消除，
    审查报告 S49）。返回 TrustDecision（含通过验签的 verified_bytes），供调用方
    原样执行；未通过时抛 PluginSignatureError。
    """

    trust_source = ""
    if config is not None and config.plugin_trust_public_key:
        trust_source = _resolve_trust_root(config, config.plugin_trust_public_key)
    if path.name != "plugin.py":
        # 单文件形态（开发期工具）：仅支持信任根对 <file>.sig 的验签（旧行为）。
        ok, reason = signing.verify_plugin(path, trust_source)
        if not ok:
            if signature_policy == SIGNATURE_POLICY_DEVELOPER:
                LOGGER.warning(
                    "开发者模式：未验签加载本地插件 %s（%s）",
                    path,
                    reason,
                )
                return None
            raise signing.PluginSignatureError(f"插件签名校验失败，拒绝加载: {path}（{reason}）")
        return None
    from . import trust as trust_model

    decision = trust_model.verify_plugin_trust(
        path.parent,
        trust_source,
        trust_model.TrustedUserList(),
        plugin_bytes=plugin_bytes,
    )
    level = decision.level
    if is_market:
        # 市场来源：仅接受维护者签名（内置信任根验签），创作者签名不足以放行。
        # 信任根缺失时（trust_root_available=False）维护者层级根本没被评估，
        # 一律拒绝——绝不把"没查到"当成"通过"（审查报告 S51）。
        if level == trust_model.TrustLevel.MaintainerSigned:
            return decision
        if signature_policy == SIGNATURE_POLICY_DEVELOPER:
            LOGGER.warning(
                "开发者模式：市场目录插件未经信任根验签，警告放行: %s（%s）",
                path,
                decision.reason,
            )
            return decision
        raise signing.PluginSignatureError(
            f"市场插件必须通过信任根验签，拒绝加载: {path}（{decision.reason}）"
        )
    if level == trust_model.TrustLevel.MaintainerSigned:
        return decision
    if level == trust_model.TrustLevel.CreatorTrusted:
        LOGGER.info("创作者信任列表命中，加载插件: %s", path)
        return decision
    if level == trust_model.TrustLevel.CreatorUntrusted and decision.creator is not None:
        creator = decision.creator
        if signature_policy == SIGNATURE_POLICY_DEVELOPER:
            LOGGER.warning(
                "开发者模式：加载未信任创作者插件 %s（作者 %s，指纹 %s）",
                path,
                creator.username,
                creator.key_fingerprint,
            )
            return decision
        if trust_prompter is not None:
            result = trust_prompter(
                path.parent.name, creator.username, creator.key_fingerprint
            )
            if result == TrustPromptResult.TRUST_AND_LOAD:
                trust_model.TrustedUserList().add(
                    creator, source="local", path_hint=f"（{path}）"
                )
                LOGGER.info(
                    "已信任作者 %s（指纹 %s）并加载插件: %s",
                    creator.username,
                    creator.key_fingerprint,
                    path,
                )
                return decision
            if result == TrustPromptResult.LOAD_ONCE:
                LOGGER.info("仅本次加载（作者未入信任列表）: %s", path)
                return decision
        raise signing.PluginSignatureError(
            f"插件 {path} 拒绝加载：作者 {creator.username}（指纹 {creator.key_fingerprint}）"
            " 未在本地信任列表。信任命令: python tools/identity.py trust add "
            f"--pubkey <作者的 .pem 公钥文件> --name {creator.username}"
        )
    if signature_policy == SIGNATURE_POLICY_DEVELOPER:
        LOGGER.warning(
            "开发者模式：未验签加载本地插件 %s（%s）；"
            "生产环境应使用 strict 策略并配置信任根",
            path,
            decision.reason,
        )
        return decision
    raise signing.PluginSignatureError(f"插件签名校验失败，拒绝加载: {path}（{decision.reason}）")
