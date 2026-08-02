from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..core.config import AppConfig, load_config, validate_config
from ..core.utils import atomic_write, utcnow
from ..pipeline import Pipeline
from ..pipeline.exporters import export_all
from ..pipeline_ops.plan_compiler import TaskPlan, compile_task_plan, diff_plans
from ..pipeline_ops.preflight import run_sample
from ..pipeline_ops.task_ir import TaskIR
from ..runtime.run_control import RunControl
from ..security.security_audit import egress_audit_report
from ..state import StateStore


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

    def _ensure_config(self) -> AppConfig:
        """Return cached config dict, reloading only when the file mtime has changed."""
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

    def run(self, *, resume: bool = False, retry_failed: bool = False, require_sample_match: bool = False) -> dict[str, Any]:
        config = self._ensure_config()
        plan = compile_task_plan(TaskIR.from_config(config.raw))
        self._require_runnable(plan)
        if require_sample_match:
            binding = self._read_binding(config)
            if binding.get("sample_plan_hash") != plan.plan_hash:
                raise ValueError("正式运行计划与最近一次试跑计划不一致，请重新试跑或明确取消绑定检查")
        self._emit("stage", "run_started", {"plan_hash": plan.plan_hash})
        with Pipeline(config) as pipeline:
            result = pipeline.run(resume=resume, retry_failed=retry_failed)
        self._write_binding(config, formal_plan_hash=plan.plan_hash)
        self._emit("stage", "run_finished", {"plan_hash": plan.plan_hash, "status": result.get("status")})
        return {**result, "plan_hash": plan.plan_hash}

    def sample(self, *, pages: int = 3) -> dict[str, Any]:
        config = self._ensure_config()
        plan = compile_task_plan(TaskIR.from_config(config.raw))
        self._require_runnable(plan)
        result = run_sample(config, pages=pages)
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
        return {"run": state_result, "security": egress_audit_report(config.workspace / "logs" / "egress-audit.jsonl")}

    def export(self, run_id: str | None = None) -> dict[str, Any]:
        config = self._ensure_config()
        with StateStore(config.workspace / "state.sqlite3") as state:
            return export_all(config, state, run_id)

    def _plan(self) -> TaskPlan:
        config = self._ensure_config()
        return compile_task_plan(TaskIR.from_config(config.raw))

    @staticmethod
    def _require_runnable(plan: TaskPlan) -> None:
        if plan.conflicts:
            raise ValueError("；".join(plan.conflicts))

    def _control(self, action: str) -> dict[str, Any]:
        config = self._ensure_config()
        control = RunControl(config.workspace)
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
