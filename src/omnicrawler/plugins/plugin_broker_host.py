"""CapabilityBroker 的宿主注入能力域 —— 由 plugin_broker.py 抽出的 Mixin（P1-3 第六批，收官）。

含四个能力域：
- ``BrokerSystemMixin``：system.info
- ``BrokerStateMixin``：state.get/set/delete/migrate（插件状态命名空间隔离）
- ``BrokerSurfacesMixin``：surface.background.* / render.html.* + 三个 ``_require_*`` 共享守卫
- ``BrokerSecretsMixin``：secrets.get（白名单约束）

以普通 Mixin 形式保留 ``self`` 语义（broker 经 ``dispatch`` 的 getattr 路由调用
``_cap_*``），宿主属性与调用点不变。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from .plugin_broker_contracts import (
    CAPABILITY_VERSIONS,
    E_CONTRACT,
    E_INTERNAL,
    E_PERMISSION,
    E_RESOURCE,
    CapabilityError,
)

LOGGER = logging.getLogger(__name__)

class BrokerSystemMixin:
    """system.info 能力域：宿主版本/平台/后端信息。"""

    _system_info: dict[str, Any]
    def _cap_system_info(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = dict(self._system_info)
        result["capability_versions"] = dict(CAPABILITY_VERSIONS)
        return result


class BrokerStateMixin:
    """state 能力域：插件状态读写/删除/迁移（命名空间隔离）。"""

    _state: Any
    _run_id: str
    _project_scope: str
    _plugin_id: str
    _plugin_author_fingerprint: str
    _plugin_state_schema: int
    def _cap_state_get(self, payload: dict[str, Any]) -> dict[str, Any]:
        state, namespace = self._plugin_state_namespace()
        key = str(payload.get("key", ""))
        try:
            found, value = state.plugin_state_get(namespace, key)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CapabilityError(E_CONTRACT, str(exc)) from exc
        return {"found": found, "value": value}

    def _cap_state_set(self, payload: dict[str, Any]) -> dict[str, Any]:
        state, namespace = self._plugin_state_namespace()
        key = str(payload.get("key", ""))
        try:
            state.plugin_state_set(namespace, key, payload.get("value"))
        except (TypeError, ValueError) as exc:
            raise CapabilityError(E_CONTRACT, str(exc)) from exc
        return {"saved": True}

    def _cap_state_delete(self, payload: dict[str, Any]) -> dict[str, Any]:
        state, namespace = self._plugin_state_namespace()
        key = str(payload.get("key", ""))
        try:
            deleted = state.plugin_state_delete(namespace, key)
        except (TypeError, ValueError) as exc:
            raise CapabilityError(E_CONTRACT, str(exc)) from exc
        return {"deleted": deleted}

    def _cap_state_migrate(self, payload: dict[str, Any]) -> dict[str, Any]:
        state, namespace = self._plugin_state_namespace()
        if str(payload.get("strategy", "copy")) != "copy":
            raise CapabilityError(E_CONTRACT, "state.migrate 当前仅支持显式 copy 策略")
        try:
            source_schema = int(str(payload.get("source_schema", "")))
            copied = state.plugin_state_copy_schema(namespace, source_schema)
        except (TypeError, ValueError) as exc:
            raise CapabilityError(E_CONTRACT, str(exc)) from exc
        return {"copied": copied, "schema_version": self._plugin_state_schema}

    def _plugin_state_namespace(self) -> tuple[Any, tuple[str, str, str, int]]:
        if self._state is None:
            raise CapabilityError(E_INTERNAL, "宿主未提供 StateStore")
        if not self._plugin_id or not self._project_scope or self._plugin_state_schema < 1:
            raise CapabilityError(E_INTERNAL, "宿主未提供完整插件状态命名空间")
        return self._state, (
            self._project_scope,
            self._plugin_id,
            self._plugin_author_fingerprint,
            self._plugin_state_schema,
        )


class BrokerSurfacesMixin:
    """surfaces/render 能力域：宿主表面与隔离渲染，含三个 _require_* 共享守卫。"""

    _permissions: set[str]
    _resource_broker: Any
    _render_broker: Any
    _surface_service: Any
    def _cap_surface_background_set(self, payload: dict[str, Any]) -> dict[str, Any]:
        surface = self._require_surface_service()
        try:
            render_handle = str(payload.get("render_handle", "")).strip()
            if render_handle:
                surface.set_rendered(self._require_render_broker(), render_handle)
            else:
                surface.set_media(
                    self._require_resource_broker(),
                    str(payload.get("handle", "")),
                    str(payload.get("relative", "")),
                )
        except ValueError as exc:
            raise CapabilityError(E_RESOURCE, str(exc)) from exc
        return {"active": True}

    def _cap_render_html_snapshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        scripted = bool(payload.get("scripted", False))
        if scripted and "render:scripted" not in self._permissions:
            raise CapabilityError(E_PERMISSION, "脚本化本地渲染需要 render:scripted 权限")
        try:
            return self._require_render_broker().snapshot_html(
                self._require_resource_broker(),
                str(payload.get("handle", "")),
                str(payload.get("relative", "")),
                width=int(payload.get("width", 1920)),
                height=int(payload.get("height", 1080)),
                scripted=scripted,
            )
        except (RuntimeError, TypeError, ValueError) as exc:
            raise CapabilityError(E_RESOURCE, str(exc)) from exc

    def _cap_render_html_live_start(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._require_render_broker().start_html_live(
                self._require_resource_broker(),
                str(payload.get("handle", "")),
                str(payload.get("relative", "")),
                width=int(payload.get("width", 1280)),
                height=int(payload.get("height", 720)),
            )
        except (RuntimeError, TypeError, ValueError) as exc:
            raise CapabilityError(E_RESOURCE, str(exc)) from exc

    def _cap_render_html_live_stop(self, payload: dict[str, Any]) -> dict[str, Any]:
        del payload
        self._require_render_broker().stop_live()
        return {"active": False}

    def _cap_surface_background_configure(self, payload: dict[str, Any]) -> dict[str, Any]:
        surface = self._require_surface_service()
        try:
            return dict(surface.configure(dict(payload)))
        except (TypeError, ValueError) as exc:
            raise CapabilityError(E_CONTRACT, str(exc)) from exc

    def _cap_surface_background_clear(self, payload: dict[str, Any]) -> dict[str, Any]:
        del payload
        surface = self._require_surface_service()
        surface.clear()
        return {"active": False}

    def _cap_surface_background_capabilities(
        self, payload: dict[str, Any]
    ) -> dict[str, Any]:
        del payload
        surface = self._require_surface_service()
        try:
            return dict(surface.capabilities())
        except (TypeError, ValueError) as exc:
            raise CapabilityError(E_CONTRACT, str(exc)) from exc

    def _require_resource_broker(self) -> Any:
        if self._resource_broker is None:
            raise CapabilityError(E_INTERNAL, "宿主未绑定资源授权服务")
        return self._resource_broker

    def _require_surface_service(self) -> Any:
        if self._surface_service is None:
            raise CapabilityError(E_INTERNAL, "当前入口未绑定媒体表面")
        return self._surface_service

    def _require_render_broker(self) -> Any:
        if self._render_broker is None:
            raise CapabilityError(E_INTERNAL, "宿主未绑定隔离渲染服务")
        return self._render_broker


class BrokerSecretsMixin:
    """secrets 能力域：受白名单约束的密钥解析。"""

    _audit_hook: Any
    _plugin_id: str
    _secret_resolver: Any
    _secrets_allowlist: set[str]
    def _cap_secrets_get(self, payload: dict[str, Any]) -> dict[str, Any]:
        """O 例外路径（方案 O2-B）：secrets.get 显式例外，默认走代理密钥零暴露。

        - manifest 必须声明 secrets 白名单（secrets_allowlist），否则拒绝
        - 仅返回白名单内的 ref；越界 → E_PERMISSION
        - 明文仅在单次调用返回，不缓存；调用即审计（decision=secret_accessed）
        """
        ref = str(payload.get("ref", "")).strip()
        if not ref:
            raise CapabilityError(E_CONTRACT, "secrets.get 需要 ref 参数")
        if ref not in self._secrets_allowlist:
            raise CapabilityError(E_PERMISSION, f"secrets ref 不在 manifest 白名单: {ref}")
        if self._secret_resolver is None:
            raise CapabilityError(E_INTERNAL, "宿主未提供密钥解析器（secrets.get 不可用）")
        value = self._secret_resolver(ref)
        if value is None:
            raise CapabilityError(E_RESOURCE, f"密钥不存在或不可读: {ref}")
        # 审计：密钥访问留痕（decision=secret_accessed，reason=ref；不记录明文）
        if self._audit_hook is not None:
            try:
                self._audit_hook(
                    "plugin.secret_accessed",
                    {"plugin_id": self._plugin_id, "decision": "secret_accessed", "reason": ref},
                )
            except Exception:  # noqa: BLE001 - 审计失败不阻断
                LOGGER.warning("密钥访问审计写入失败: plugin=%s ref=%s", self._plugin_id, ref)
        return {"value": value}
