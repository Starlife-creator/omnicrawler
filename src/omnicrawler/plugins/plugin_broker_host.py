"""CapabilityBroker 的宿主注入能力域 —— 由 plugin_broker.py 抽出的 Mixin（P1-3 第六批，收官）。

含四个能力域：
- ``BrokerSystemMixin``：system.info
- ``BrokerStateMixin``：state.get/set/delete/migrate（插件状态命名空间隔离）
- ``BrokerSurfacesMixin``：surface.background.* / render.html.* + 三个 ``_require_*`` 共享守卫
- ``BrokerSecretsMixin``：secrets.get（白名单约束）
- ``BrokerThemeMixin``：theme.apply（运行期切换**已注册**主题，2026-11-27）

以普通 Mixin 形式保留 ``self`` 语义（broker 经 ``dispatch`` 的 getattr 路由调用
``_cap_*``），宿主属性与调用点不变。
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from .plugin_broker_contracts import (
    CAPABILITY_VERSIONS,
    E_CONTRACT,
    E_INTERNAL,
    E_PERMISSION,
    E_RESOURCE,
    CapabilityError,
    is_sensitive_state_key,
)

LOGGER = logging.getLogger(__name__)

class BrokerSystemMixin:
    """system.info 能力域：宿主版本/平台/后端信息。"""

    _system_info: dict[str, Any]
    def _cap_system_info(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = dict(self._system_info)
        result["capability_versions"] = dict(CAPABILITY_VERSIONS)
        return result


class BrokerProgressMixin:
    """view.progress 能力域（U6，2026-09-24）：插件运行期向宿主 GUI 推送批处理进度。

    线程契约：本处理器跑在 pipeline 工作线程（drive_loop 的 capability 应答路径），
    ``_progress_relay`` 必须是**线程安全的一次投递**（Qt 信号 emit，自动跨线程排队）
    ——GUI 更新由接收方在其所属线程完成，本处理器绝不直接触碰任何 QWidget。
    无接收方（面板未安装 / 插件无 view 类型）时静默降级为 delivered=False；
    投递异常同样不阻断插件运行（进度是可丢弃的通知，不是契约性数据）。
    """

    _progress_relay: Any
    _plugin_id: str
    _PROGRESS_FIELDS = frozenset(
        {"done", "total", "current_doi", "eta_seconds", "success", "failed"}
    )

    def _cap_view_progress(self, payload: dict[str, Any]) -> dict[str, Any]:
        relay = getattr(self, "_progress_relay", None)
        if relay is None:
            return {"delivered": False, "reason": "no-view-panel"}
        unknown = set(payload) - self._PROGRESS_FIELDS
        if unknown:
            raise CapabilityError(E_CONTRACT, f"view.progress 包含未知字段: {sorted(unknown)}")
        data = dict(payload)
        try:
            data["done"] = int(data.get("done", 0))
            data["total"] = int(data.get("total", 0))
        except (TypeError, ValueError) as exc:
            raise CapabilityError(E_CONTRACT, f"view.progress done/total 必须是整数: {exc}") from exc
        data["plugin_id"] = self._plugin_id
        try:
            relay(data)
        except Exception as exc:  # noqa: BLE001 - 推送失败绝不阻断插件运行
            LOGGER.warning(
                "view.progress 投递失败（不阻断运行）: plugin=%s: %s", self._plugin_id, exc
            )
            return {"delivered": False, "reason": "relay-error"}
        return {"delivered": True}


class BrokerStateMixin:
    """state 能力域：插件状态读写/删除/迁移（命名空间隔离）。"""

    @staticmethod
    def _require_non_sensitive_state_key(key: str) -> None:
        if is_sensitive_state_key(key):
            raise CapabilityError(
                E_PERMISSION,
                "Cookie、会话或凭据不得存入插件 state；请使用宿主 auth 注入",
            )

    _state: Any
    _run_id: str
    _project_scope: str
    _plugin_id: str
    _plugin_author_fingerprint: str
    _plugin_state_schema: int
    def _cap_state_get(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_non_sensitive_state_key(str(payload.get("key", "")))
        state, namespace = self._plugin_state_namespace()
        key = str(payload.get("key", ""))
        try:
            found, value = state.plugin_state_get(namespace, key)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CapabilityError(E_CONTRACT, str(exc)) from exc
        return {"found": found, "value": value}

    def _cap_state_set(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_non_sensitive_state_key(str(payload.get("key", "")))
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
            copied, skipped_sensitive = state.plugin_state_copy_schema(
                namespace, source_schema, exclude_keys=is_sensitive_state_key
            )
        except (TypeError, ValueError) as exc:
            raise CapabilityError(E_CONTRACT, str(exc)) from exc
        return {
            "copied": copied,
            "skipped_sensitive": skipped_sensitive,
            "schema_version": self._plugin_state_schema,
        }

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
    # 前景层服务（`surface.foreground.*`）。与 `_surface_service` 并列而非合并 ——
    # 两者语义相反（下沉 vs 上浮），合成一个对象每个方法都要分叉。
    _foreground_service: Any

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

    # ---- surface.foreground.*（2026-11-28）----
    #
    # ★ 与 background 的**关键差别**：前景层没有 `render_handle` 之外的输入面。
    #   背景可以接受「媒体句柄」或「渲染帧」两种来源；前景**只接受渲染帧与纯色**，
    #   因为浮层盖在内容之上，一张用户媒体图会让界面完全不可读。

    def _cap_surface_foreground_set(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_foreground_service()
        try:
            render_handle = str(payload.get("render_handle", "")).strip()
            if render_handle:
                return dict(
                    service.show_rendered(self._require_render_broker(), render_handle)
                )
            color = str(payload.get("color", "")).strip()
            if not color:
                raise CapabilityError(
                    E_CONTRACT,
                    "surface.foreground.set 需要 render_handle 或 color（前景层不接受用户媒体文件）",
                )
            return dict(service.show_solid(color, int(payload.get("opacity", 100))))
        except (TypeError, ValueError) as exc:
            raise CapabilityError(E_CONTRACT, str(exc)) from exc

    def _cap_surface_foreground_configure(self, payload: dict[str, Any]) -> dict[str, Any]:
        service = self._require_foreground_service()
        try:
            return dict(service.configure(dict(payload)))
        except (TypeError, ValueError) as exc:
            raise CapabilityError(E_CONTRACT, str(exc)) from exc

    def _cap_surface_foreground_clear(self, payload: dict[str, Any]) -> dict[str, Any]:
        del payload
        return dict(self._require_foreground_service().clear())

    def _cap_surface_foreground_capabilities(
        self, payload: dict[str, Any]
    ) -> dict[str, Any]:
        del payload
        return dict(self._require_foreground_service().capabilities())

    def _require_foreground_service(self) -> Any:
        service = getattr(self, "_foreground_service", None)
        if service is None:
            raise CapabilityError(E_INTERNAL, "当前入口未绑定前景层服务（surface.foreground 不可用）")
        return service

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


#: 宿主注册的「应用主题」回调（`theme_id -> bool`）。
#:
#: ★ 为什么不把 `theme_service` 一路塞进 `CapabilityBroker.__init__`：
#:   那需要改 4 层构造链（adapter → session host → broker → mixin），而 GUI 侧
#:   只有一个注册点。用模块级注册表把「谁实现了」与「谁调用」解耦，
#:   **成本低、且没有一条新的构造参数需要所有调用方跟进**。
#:   None ⇒ `theme.apply` 明确报 E_INTERNAL，不是静默成功。
_THEME_APPLY_HANDLER: Callable[[str], bool] | None = None


def register_theme_apply_handler(handler: Callable[[str], bool] | None) -> None:
    """注册/注销宿主侧主题应用回调（GUI 启动时注册，退出时置 None）。"""
    global _THEME_APPLY_HANDLER
    _THEME_APPLY_HANDLER = handler


def theme_apply_handler() -> Callable[[str], bool] | None:
    """当前注册的主题应用回调（未注册时为 None）。"""
    return _THEME_APPLY_HANDLER


class BrokerThemeMixin:
    """theme.apply 能力域（2026-11-27）：运行期切换**已注册**的插件主题。

    为什么必须新增一条能力，而不是复用 `ui:theme`
    ------------------------------------------------
    `ui:theme` 是**加载期**声明：插件在 `register(registry)` 里注册一个
    `ThemeRegistration`，宿主启动时套用。它对**本地**插件是**自动批准**的
    （`plugin_loader` 里 `approved |= UI_PERMISSIONS`）。

    如果运行期也走 `ui:theme`，等于「任何本地插件无需审批即可随时改整个界面的
    配色」—— 那是权限提升，不是功能。所以运行期走独立的 `theme:apply`：
    它**不在** `UI_PERMISSIONS` 里，必须由用户显式授予。

    边界：只接受宿主侧**已注册**的 `theme_id`，不接受任意色值。
    插件不能借这条能力渲染任意界面。
    """

    _permissions: set[str]

    def _cap_theme_apply(self, payload: dict[str, Any]) -> dict[str, Any]:
        theme_id = str(payload.get("theme_id", "")).strip().lower()
        if not theme_id:
            raise CapabilityError(E_CONTRACT, "theme.apply 需要 theme_id 参数")
        handler = theme_apply_handler()
        if handler is None:
            raise CapabilityError(E_INTERNAL, "当前入口未绑定主题服务（theme.apply 不可用）")
        try:
            applied = bool(handler(theme_id))
        except ValueError as exc:
            raise CapabilityError(E_CONTRACT, str(exc)) from exc
        if not applied:
            # ★ 不静默忽略：插件作者需要知道「你以为注册了但宿主没认」。
            raise CapabilityError(
                E_RESOURCE,
                f"主题 {theme_id} 未在宿主注册；插件必须先注册 ThemeRegistration，"
                "否则不能在运行期切换",
            )
        return {"applied": True, "theme_id": theme_id}


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

class BrokerComponentOcrMixin:
    """component.ocr.recognize 能力域（2026-11-17，文档工作台插件化）。

    为什么由宿主代理而不是插件自己调组件
    ------------------------------------
    组件验签依赖 ``configs/component_trust.pub.pem``，租赁依赖跨进程注册表锁，
    启动组件子进程还要裁剪环境变量。这三件事都是**宿主特权**：
    插件是受沙箱约束的子进程，一旦让它自己做，等于把安全边界下移到
    不受控进程里 —— 与仓库「安全与隔离」原则直接冲突。

    因此本能力的边界是：

    * 插件只交出 **PNG 字节** 与 **引擎名**；
    * 宿主完成验签、租赁、子进程调用、输出裁剪；
    * 回传**只有** text / confidence / engine / version；
    * 插件**永远看不到**组件路径、信任根、注册表。

    输入校验（fail-closed）
    -----------------------
    PNG 魔数 + 大小上限由宿主**再验一次**（不信任插件侧的自我约束）：
    插件同样受沙箱限制，但边界检查不该只有一处。
    """

    #: 单次 OCR 的 PNG 上限；与组件运行入口的限制一致。
    _OCR_MAX_PNG_BYTES = 20 * 1024 * 1024

    #: PNG 魔数（组件运行入口同样要求）。
    _PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

    def _cap_component_ocr_recognize(self, payload: dict[str, Any]) -> dict[str, Any]:
        engine = str(payload.get("engine", "")).strip().lower()
        if not engine:
            raise CapabilityError(E_CONTRACT, "component.ocr.recognize 需要 engine 参数")
        component = str(payload.get("component", "")).strip()
        if not component:
            raise CapabilityError(E_CONTRACT, "component.ocr.recognize 需要 component 参数")

        raw = payload.get("png_base64", "")
        if not isinstance(raw, str) or not raw:
            raise CapabilityError(E_CONTRACT, "component.ocr.recognize 需要 png_base64")
        import base64

        try:
            png = base64.b64decode(raw, validate=True)
        except Exception as exc:  # noqa: BLE001 - 统一归为参数错误
            raise CapabilityError(E_CONTRACT, f"png_base64 不是合法 base64: {exc}") from exc
        if not png.startswith(self._PNG_MAGIC):
            raise CapabilityError(E_CONTRACT, "输入不是 PNG（魔数不符）")
        if len(png) > self._OCR_MAX_PNG_BYTES:
            raise CapabilityError(
                E_RESOURCE,
                f"PNG 超过上限：{len(png)} > {self._OCR_MAX_PNG_BYTES} 字节",
            )

        # 只有声明了 component:ocr 的插件才会走到这里（权限在路由层已校验）。
        #
        # 注意 `leased_ocr_runtime` 只返回**路径与版本**（OCRRuntime 是 dataclass），
        # 真正调用组件可执行文件的是 `ComponentOCRBackend`——它内部再走一次
        # 租赁、校验版本、裁剪环境变量、限制 PNG 输入。这里不重复那套逻辑，
        # 避免出现"两份组件调用实现"（那正是最难保持一致的那种重复）。
        #
        # **经宿主 facade 取后端**而不是直接 import `pdfx`：PDF 工作台正在被
        # 迁成插件，`plugins`（支柱②）不该硬依赖它（边界由
        # `tests/unit/tools/test_pillar_boundary.py` 钉住）。
        from ..services.document_facade import ocr_backend_class

        try:
            backend = ocr_backend_class()(
                {"component": component, "backend": engine,
                 "lang": str(payload.get("lang", "ch"))}
            )
            result = backend.recognize_rich(png)
        except ValueError as exc:
            # 缺信任根 / 组件未安装 / 验签失败 / 版本已变 —— 都是**可解释**的失败
            raise CapabilityError(E_RESOURCE, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - 组件崩溃不能让宿主进程倒下
            LOGGER.warning("组件 OCR 调用失败: component=%s engine=%s", component, engine)
            raise CapabilityError(E_INTERNAL, f"组件 OCR 调用失败: {exc}") from exc

        text = str(getattr(result, "text", "") or "")
        confidence = getattr(result, "confidence", None)
        return {
            "text": text,
            "confidence": confidence,
            "engine": engine,
            "component": component,
        }
