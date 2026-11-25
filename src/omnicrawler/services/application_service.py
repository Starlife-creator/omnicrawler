from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

# ★ 2026-11-25：`Pipeline` / `export_all` / `run_sample` / `RunControl` /
# `session_lease` / `egress_audit_report` 改为**函数内惰性导入**。
#
# 为什么：`..pipeline` 整条导入链实测 **342 ms**（含 bs4 66ms、core.config 60ms、
# extraction 75ms…），而本模块被 `gui/main.py` 在**模块顶层**导入。
# 结果是：用户双击图标 → 还没看到任何界面，就先付了这 342ms。
#
# 这些名字只在下面几个方法里用到（run/sample/export/resume…），
# 而 GUI 冷启动时一个都不调用。**能推迟到真正需要时再付的，就不要在导入期付。**
#
# 类型标注需要的名字放 `TYPE_CHECKING`。
from typing import TYPE_CHECKING, Any

from ..core.config import AppConfig, load_config, validate_config
from ..core.utils import atomic_write, utcnow
from ..pipeline_ops.plan_compiler import TaskPlan, compile_task_plan, diff_plans
from ..pipeline_ops.task_ir import TaskIR
from ..state import StateStore

if TYPE_CHECKING:  # 仅类型标注；运行时由下面的 __getattr__ 按需加载
    pass


#: 惰性模块属性表：名字 -> (模块路径, 属性名)。
#:
#: ★ 2026-11-25：为什么用模块级 `__getattr__`（PEP 562）而不是函数内 import。
#:
#: 函数内导入虽然也省了导入时间，但**破坏了两件事**：
#:
#: 1. `from ...application_service import Pipeline` 不再成立（模块上没有这个属性）；
#: 2. `monkeypatch.setattr(module, "Pipeline", Fake)` 失效 —— 函数内的
#:    `from ..pipeline import Pipeline` 不经过模块属性查找，patch 打不中。
#:    本会话实测：这个改动让 `test_run_finished_event.py` 的两条守卫直接报
#:    `AttributeError: module has no attribute Pipeline`。
#:
#: `__getattr__` 两者都保住：属性第一次被访问时才真正 import，之后缓存在 globals 里。
#: 于是「导入期零成本」与「模块属性可 patch」同时成立。
_LAZY_ATTRS: dict[str, tuple[str, str]] = {
    "Pipeline": ("..pipeline", "Pipeline"),
    "export_all": ("..pipeline.exporters", "export_all"),
    "run_sample": ("..pipeline_ops.preflight", "run_sample"),
    "RunControl": ("..runtime.run_control", "RunControl"),
    "session_lease": ("..fetching.session_lease", "session_lease"),
    "egress_audit_report": ("..security.security_audit", "egress_audit_report"),
}


def _lazy(name: str) -> Any:
    """取一个惰性模块属性，**先查 `globals()`**。

    为什么先查 globals：`monkeypatch.setattr(module, "Pipeline", Fake)` 写的就是
    `module.__dict__`，也就是本模块的 `globals()`。先查它，patch 才打得中。

    注意：模块级 `__getattr__`（PEP 562）只对**模块属性访问**生效；
    函数体里的裸名字 `Pipeline(...)` 走的是 globals 查找，**不经过 `__getattr__`**。
    所以函数体内必须显式调用本函数取值，不能指望 `__getattr__`。
    """
    existing = globals().get(name)
    if existing is not None:
        return existing
    module_path, attr = _LAZY_ATTRS[name]
    import importlib

    value = getattr(importlib.import_module(module_path, __package__), attr)
    globals()[name] = value
    return value


def __getattr__(name: str) -> Any:
    """按需加载重模块属性（PEP 562），供 `from ...application_service import X`。

    只有 `_LAZY_ATTRS` 里的名字会被拦；其余名字抛 `AttributeError`（保持正常语义）。
    """
    if name not in _LAZY_ATTRS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return _lazy(name)


@dataclass(frozen=True, slots=True)
class ApplicationEvent:
    category: str
    name: str
    timestamp: str
    payload: dict[str, Any]


EventSink = Callable[[dict[str, Any]], None]


class ApplicationService:
    """Public-shaped application boundary shared by CLI, GUI and the future SDK."""

    def __init__(self, config_path: str | Path, *, event_sink: EventSink | None = None) -> None:
        self.config_path = Path(config_path).expanduser().resolve()
        self.event_sink = event_sink
        self._config_cache: AppConfig | None = None
        self._config_signature: tuple[int, int] | None = None
        # B-P3-6：配置缓存按 mtime/size 判定，跨线程 reload 竞争由 RLock 串行化。
        self._config_lock = threading.RLock()

    def _ensure_config(self) -> AppConfig:
        """Return cached config dict, reloading only when the file mtime has changed."""
        with self._config_lock:
            try:
                stat = self.config_path.stat()
                signature = (stat.st_mtime_ns, stat.st_size)
            except OSError:
                signature = (0, 0)
            if self._config_cache is None or signature != self._config_signature:
                self._config_cache = load_config(self.config_path)
                self._config_signature = signature
            return self._config_cache

    def reload(self) -> None:
        """Reset the cached config so the next access re-reads the file."""
        with self._config_lock:
            self._config_cache = None
            self._config_signature = None

    def load(self) -> dict[str, Any]:
        config = self._ensure_config()
        return {"config": _public_config(config), "ir": TaskIR.from_config(config.raw).to_mapping()}

    def validate(self) -> dict[str, Any]:
        config = self._ensure_config()
        errors, warnings = validate_config(config)
        plan = compile_task_plan(TaskIR.from_config(config.raw))
        return {
            "ok": not errors and not plan.conflicts,
            "errors": [*errors, *plan.conflicts],
            "warnings": [*warnings, *plan.warnings],
            "plan_hash": plan.plan_hash,
        }

    def compile(self, *, available_capabilities: list[str] | None = None) -> dict[str, Any]:
        config = self._ensure_config()
        plan = compile_task_plan(TaskIR.from_config(config.raw), available_capabilities=available_capabilities)
        self._emit("stage", "plan_compiled", {"plan_hash": plan.plan_hash, "conflicts": list(plan.conflicts)})
        return plan.to_mapping()

    def diff(self, other_config: str | Path) -> dict[str, Any]:
        before = self._plan()
        other = ApplicationService(other_config)._plan()
        return {"before_hash": before.plan_hash, "after_hash": other.plan_hash, "changes": diff_plans(before, other)}

    def run(self, *, resume: bool = False, retry_failed: bool = False, require_sample_match: bool = False,
            max_pages: int | None = None, callback: Callable[[str, dict[str, Any]], None] | None = None,
            lifecycle_callback: Callable[[str, dict[str, Any]], None] | None = None) -> dict[str, Any]:
        # E9：max_pages 与 callback 透传 Pipeline.run，保证任意调用方（CLI/GUI）行为一致
        config = self._ensure_config()
        plan = compile_task_plan(TaskIR.from_config(config.raw))
        self._require_runnable(plan)
        if require_sample_match:
            binding = self._read_binding(config)
            if binding.get("sample_plan_hash") != plan.plan_hash:
                raise ValueError("正式运行计划与最近一次试跑计划不一致，请重新试跑或明确取消绑定检查")
        self._emit("stage", "run_started", {"plan_hash": plan.plan_hash})
        pipeline_cls = _lazy("Pipeline")
        status = "unknown"
        try:
            with pipeline_cls(config) as pipeline:
                if lifecycle_callback is not None:
                    target = lifecycle_callback
                    # Progress callbacks and lifecycle hooks are separate
                    # channels. Register on this pipeline instance only.
                    def fetched(**context: Any) -> None:
                        target("after_fetch", {"engine": context.get("engine"),
                                               "escalated": bool(context.get("escalated", False))})
                    def extracted(**context: Any) -> None:
                        from ..quality.output_metrics import record_quality_counts
                        counts = record_quality_counts(getattr(record, "evidence", {}) for record in context.get("records", []))
                        target("after_extract", {"count": context.get("count", 0), "output_quality": counts})
                    pipeline.registry.register_hook("after_fetch", fetched)
                    pipeline.registry.register_hook("after_extract", extracted)
                result = pipeline.run(resume=resume, retry_failed=retry_failed, max_pages=max_pages, callback=callback)
            self._write_binding(config, formal_plan_hash=plan.plan_hash)
            result_status = result.get("status")
            status = result_status if isinstance(result_status, str) else "unknown"
            return {**result, "plan_hash": plan.plan_hash}
        finally:
            # S2.5.36：异常路径也发 run_finished，监听方不再永久等待
            self._emit("stage", "run_finished", {"plan_hash": plan.plan_hash, "status": status})

    def sample(self, *, pages: int = 3) -> dict[str, Any]:
        config = self._ensure_config()
        plan = compile_task_plan(TaskIR.from_config(config.raw))
        self._require_runnable(plan)
        result = _lazy("run_sample")(config, pages=pages)
        self._write_binding(config, sample_plan_hash=plan.plan_hash)
        self._emit("progress", "sample_finished", {"plan_hash": plan.plan_hash, "pages": pages})
        return {**result, "plan_hash": plan.plan_hash}

    def pause(self) -> dict[str, Any]:
        return self._control("pause")

    def resume(self) -> dict[str, Any]:
        return self._control("resume")

    def stop(self) -> dict[str, Any]:
        return self._control("stop")

    def query(self) -> dict[str, Any]:
        config = self._ensure_config()
        database = config.workspace / "state.sqlite3"
        state_result: dict[str, Any] = {"status": "not_started"}
        if database.is_file():
            with StateStore(database) as state:
                state_result = {"latest_run": state.latest_run(), "totals": state.stats()}
        return {"run": state_result, "security": _lazy("egress_audit_report")(config.workspace / "logs" / "egress-audit.jsonl")}

    def export(self, run_id: str | None = None) -> dict[str, Any]:
        config = self._ensure_config()
        database = config.workspace / "state.sqlite3"
        if not database.is_file():
            # S2.5.35：空库显式报错，不再静默创建空库返回空结果
            raise FileNotFoundError(
                f"断点数据库不存在: {database}。请先运行采集任务（omnicrawler run -c <配置>）再导出。"
            )
        with _lazy("session_lease")(config.workspace), StateStore(database) as state:
            delivery = None
            if config.section("download").get("verified_pdf_manifest", False):
                from ..pipeline_ops.provenance import write_pdf_source_manifest
                delivery = write_pdf_source_manifest(config.workspace, state, run_id=run_id)
            result = _lazy("export_all")(config, state, run_id)
            if delivery is not None:
                result["pdf_delivery"] = delivery
            return result

    def _plan(self) -> TaskPlan:
        config = self._ensure_config()
        return compile_task_plan(TaskIR.from_config(config.raw))

    @staticmethod
    def _require_runnable(plan: TaskPlan) -> None:
        if plan.conflicts:
            raise ValueError("；".join(plan.conflicts))

    def _control(self, action: str) -> dict[str, Any]:
        config = self._ensure_config()
        control = _lazy("RunControl")(config.workspace)
        result = {"pause": control.pause, "resume": control.resume, "stop": control.request_stop}[action]()
        self._emit("stage", action, result)
        return result

    def _emit(self, category: str, name: str, payload: dict[str, Any]) -> None:
        event = asdict(ApplicationEvent(category, name, utcnow(), payload))
        if self.event_sink:
            self.event_sink(event)

    @staticmethod
    def _binding_path(config: AppConfig) -> Path:
        return config.workspace / "plan-bindings.json"

    def _read_binding(self, config: AppConfig) -> dict[str, Any]:
        try:
            value = json.loads(self._binding_path(config).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write_binding(self, config: AppConfig, **values: Any) -> None:
        payload = {**self._read_binding(config), **values, "updated_at": utcnow()}
        atomic_write(self._binding_path(config), json.dumps(payload, ensure_ascii=False, indent=2).encode())


def _public_config(config: AppConfig) -> dict[str, Any]:
    return {"path": str(config.path), "root": str(config.root), "workspace": str(config.workspace), "project_name": config.project_name, "source_kind": config.source_kind}
