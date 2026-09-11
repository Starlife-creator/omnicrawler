"""CapabilityBroker 的外部 I/O 能力域 —— 由 plugin_broker.py 抽出的 Mixin（P1-3 第五批）。

含两个能力域：
- ``BrokerFilesMixin``：temp.open / files.read / resources.{describe,enumerate,read}
- ``BrokerNetworkMixin``：network.fetch（经 egress 策略与日配额）+ 读写共现审计

以普通 Mixin 形式保留 ``self`` 语义（broker 经 ``dispatch`` 的 getattr 路由调用
``_cap_*``），宿主属性与调用点不变。``_require_resource_broker`` 等共享守卫留在宿主。
"""
from __future__ import annotations

import base64
import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .plugin_broker_contracts import (
    E_CONTRACT,
    E_EGRESS_BLOCKED,
    E_PERMISSION,
    E_QUOTA,
    E_RESOURCE,
    CapabilityError,
)

LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .plugin_quota import DailyNetworkQuota


class BrokerFilesMixin:
    """temp / files / resources 能力域。"""

    # ---- 宿主契约：实例属性 ----
    _input_files: tuple[str, ...]
    _temp_dir: Path | None
    _temp_root: Path
    temp_files_written: list[str]

    # ---- 宿主契约：方法（broker 级别的共享守卫）----
    if TYPE_CHECKING:
        def _require_resource_broker(self) -> Any: ...
    def _cap_temp_open(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._temp_dir is None:
            self._temp_dir = Path(
                tempfile.mkdtemp(prefix="omnicrawler-plugin-", dir=self._temp_root)
            )
        name = str(payload.get("name", "")).strip()
        if not name or "/" in name or "\\" in name or name.startswith(".."):
            raise CapabilityError(E_CONTRACT, "temp.open 文件名非法")
        target = self._temp_dir / name
        if payload.get("content_b64") is not None:
            import base64

            target.write_bytes(base64.b64decode(str(payload["content_b64"])))
            self.temp_files_written.append(name)
        return {"path": str(target)}

    def _cap_files_read(self, payload: dict[str, Any]) -> dict[str, Any]:
        """files:read（Phase 2b 正式化）：manifest input_files 白名单 + 逃逸拒绝。

        - 请求路径必须命中白名单（精确文件条目 或 目录条目前缀）
        - 解析（含符号链接）后目标必须仍落在白名单根内——链接指向库外 → 拒
        """
        path = str(payload.get("path", ""))
        if not path:
            raise CapabilityError(E_CONTRACT, "files.read 需要 path 参数")
        allowed = [str(item) for item in self._input_files]
        # 白名单命中：精确文件 或 目录前缀（目录条目尾斜杠容忍）
        hit_root: str | None = None
        for item in allowed:
            if path == item:
                hit_root = item
                break
            if path.startswith(item.rstrip("/\\") + "/") or path.startswith(
                item.rstrip("/\\") + "\\"
            ):
                hit_root = item
                break
        if hit_root is None:
            raise CapabilityError(E_PERMISSION, f"路径不在 input_files 白名单: {path}")
        try:
            candidate = Path(path).resolve(strict=True)
        except OSError as exc:
            raise CapabilityError(E_RESOURCE, f"路径解析失败: {exc}") from exc
        # 逃逸校验：解析后目标必须在命中白名单根的解析目录内
        root = Path(hit_root).resolve(strict=False)
        if candidate != root and root not in candidate.parents:
            raise CapabilityError(
                E_PERMISSION,
                f"路径经解析后逃逸白名单: {path} → {candidate}（命中 {hit_root}）",
            )
        try:
            data = candidate.read_bytes()
        except OSError as exc:
            raise CapabilityError(E_RESOURCE, f"读取失败: {exc}") from exc
        import base64

        return {"content_b64": base64.b64encode(data).decode("ascii"), "size": len(data)}

    def _cap_resources_describe(self, payload: dict[str, Any]) -> dict[str, Any]:
        broker = self._require_resource_broker()
        try:
            return broker.describe(str(payload.get("handle", "")))
        except ValueError as exc:
            raise CapabilityError(E_RESOURCE, str(exc)) from exc

    def _cap_resources_enumerate(self, payload: dict[str, Any]) -> dict[str, Any]:
        broker = self._require_resource_broker()
        try:
            items = broker.enumerate(
                str(payload.get("handle", "")),
                relative=str(payload.get("relative", "")),
                recursive=bool(payload.get("recursive", False)),
                limit=int(payload.get("limit", 500)),
            )
        except (TypeError, ValueError) as exc:
            raise CapabilityError(E_RESOURCE, str(exc)) from exc
        return {"items": items, "count": len(items)}

    def _cap_resources_read(self, payload: dict[str, Any]) -> dict[str, Any]:
        broker = self._require_resource_broker()
        try:
            data = broker.read(
                str(payload.get("handle", "")),
                str(payload.get("relative", "")),
                maximum_bytes=int(payload.get("maximum_bytes", 4 * 1024 * 1024)),
            )
        except (TypeError, ValueError) as exc:
            raise CapabilityError(E_RESOURCE, str(exc)) from exc
        return {"content_b64": base64.b64encode(data).decode("ascii"), "size": len(data)}


class BrokerNetworkMixin:
    """network 能力域：egress 策略 + 日配额 + 读写共现审计。"""

    # ---- 宿主契约：实例属性 ----
    _audit_hook: Any
    _daily_quota: DailyNetworkQuota | None
    _egress_policy: str
    _network: Any
    _plugin_id: str
    op_counts: dict[str, int]
    def _cap_network_fetch(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._network is None:
            raise CapabilityError(E_PERMISSION, "会话未授予网络能力（domains 未声明？）")
        url = str(payload.get("url", ""))
        if not url.startswith(("http://", "https://")):
            raise CapabilityError(E_CONTRACT, "network.fetch 仅支持 http(s) URL")
        from ..core.errors import EgressBudgetExceededError, EgressDisabledError

        # Phase 2b D4.4：日级配额检查（E_QUOTA）——与 EgressBroker maximum_requests
        # 会话级配额构成双层量约束。
        if self._daily_quota is not None:
            from .plugin_quota import QuotaExceededError

            try:
                self._daily_quota.check(self._plugin_id)
            except QuotaExceededError as exc:
                raise CapabilityError(E_QUOTA, str(exc)) from exc

        # Phase 2b J2：data_egress_policy 共现检测——records.read 后 fetch 即
        # 潜在数据外传；默认 prompt 提示，block 档阻断（E_EGRESS_BLOCKED）。
        read_calls = (
            self.op_counts.get("records.read", 0)
            + self.op_counts.get("records.page", 0)
            + self.op_counts.get("responses.page", 0)
            + self.op_counts.get("responses.payload", 0)
        )
        if read_calls > 0:
            if self._egress_policy == "block":
                raise CapabilityError(
                    E_EGRESS_BLOCKED,
                    f"data_egress_policy=block：插件在读取 records 后请求网络"
                    f"（共现次数 {read_calls}），阻断数据外传通道",
                )
            self._audit_call_cooccurrence(read_calls)

        try:
            result = self._network.fetch(
                url,
                method=str(payload.get("method", "GET")),
                headers={str(k): str(v) for k, v in (payload.get("headers") or {}).items()},
            )
        except (EgressDisabledError, EgressBudgetExceededError) as exc:
            raise CapabilityError(E_PERMISSION, f"egress 策略拒绝: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - 网络异常收敛为协议错误
            raise CapabilityError(E_RESOURCE, f"请求失败: {exc}") from exc
        finally:
            # 成功/失败都计配额（防恶意重试刷配额；字节仅成功时计）
            if self._daily_quota is not None:
                self._daily_quota.account(
                    self._plugin_id, requests=1, bytes_=0
                )
        import base64

        if self._daily_quota is not None:
            self._daily_quota.account(self._plugin_id, requests=0, bytes_=len(result.body))

        return {
            "status": result.status,
            "url": result.final_url,
            "body_b64": base64.b64encode(result.body).decode("ascii"),
        }

    def _audit_call_cooccurrence(self, read_calls: int) -> None:
        """共现风险留痕（H1 egress_cooccurrence_risk_total 口径的 broker 侧）。"""
        if self._audit_hook is not None:
            try:
                self._audit_hook(
                    "plugin.egress_cooccurrence",
                    {
                        "plugin_id": self._plugin_id,
                        "decision": "cooccurrence_risk",
                        "records_read_before": read_calls,
                    },
                )
            except Exception:  # noqa: BLE001 - 审计失败不阻断
                LOGGER.warning(
                    "共现风险审计写入失败: plugin=%s reads=%s", self._plugin_id, read_calls
                )
