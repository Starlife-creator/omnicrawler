"""GUI task actions reuse production services and bind the saved configuration."""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.config import load_config


@dataclass(frozen=True)
class TaskAction:
    name: str
    config_path: Path
    config_sha256: str
    arguments: dict[str, Any]


def repair_binding(evidence: Path, candidate: Path) -> tuple[str, tuple[bytes, bytes]]:
    digest = hashlib.sha256()
    payloads = []
    for path in (evidence, candidate):
        with path.open("rb") as handle:
            payload = handle.read(2 * 1024**2 + 1)
        if len(payload) > 2 * 1024**2:
            raise ValueError("修复输入超过大小限制")
        digest.update(str(path.resolve()).encode())
        digest.update(payload)
        payloads.append(payload)
    return digest.hexdigest(), (payloads[0], payloads[1])


def _manifest_digest(path: Path) -> str:
    with path.open("rb") as handle:
        payload = handle.read(2 * 1024**2 + 1)
    if len(payload) > 2 * 1024**2:
        raise ValueError("交付清单超过大小限制")
    return hashlib.sha256(payload).hexdigest()


def execute(action: TaskAction) -> dict[str, Any]:
    if hashlib.sha256(action.config_path.read_bytes()).hexdigest() != action.config_sha256:
        raise ValueError("已保存配置已变化，请重新打开任务工具")
    args = action.arguments
    if action.name == "browser:probe":
        from .browser_diagnostics import probe

        return probe(load_config(action.config_path))
    if action.name.startswith("references:"):
        from . import workspace_references

        path = Path(str(args.get("config") or action.config_path))
        if action.name == "references:inspect":
            return workspace_references.inspect(path)
        values = (path, str(args.get("field", "")), Path(str(args.get("source", ""))), str(args.get("mode", "copy")))
        if action.name == "references:preview":
            return workspace_references.preview(*values)
        if action.name == "references:apply" and args.get("confirmed") is True:
            return workspace_references.apply(*values, binding=str(args.get("binding", "")))
        raise ValueError("请预览并确认引用修复")
    if action.name.startswith("components:"):
        from .component_tools import execute as component_action
        return component_action(action.name.partition(":")[2], args)
    if action.name.startswith("workspace:"):
        from ..commands.workspace import execute as workspace_action
        operation = action.name.partition(":")[2]
        if operation == "import" and args.get("confirmed") is not True:
            raise ValueError("请确认工作区包和新的导入目录")
        return workspace_action(str(action.config_path), operation, target=str(args.get("target", "")),
                                destination=str(args.get("destination", "")), kind=str(args.get("kind", "full")))
    if action.name.startswith("notifications:"):
        from .record_notifications import dispatch, report
        config = load_config(action.config_path)
        if action.name == "notifications:retry":
            if args.get("confirmed") is not True:
                raise ValueError("请确认补发明确选中的通知")
            return dispatch(config, force=True, event_ids=set(args.get("event_ids", [])))
        return report(config)
    if action.name == "workflow":
        from .workflow_diagnostics import describe
        return describe(load_config(action.config_path), run_id=str(args.get("run_id", "")))
    if action.name == "replay":
        from ..state import StateStore
        from .replay import replay_field
        from .workflow_diagnostics import read_runtime
        config = load_config(action.config_path)
        run_id, field_name = str(args.get("run_id", "")), str(args.get("field", ""))
        if not run_id or not field_name:
            raise ValueError("请选择运行并填写字段名")
        if read_runtime(config, run_id=run_id).get("run_id") != run_id:
            raise ValueError("运行记录不可用")
        with StateStore(config.workspace / "state.sqlite3") as state:
            result = replay_field(run_id, field_name, store=state)
        return {**result, "historical_content": True, "candidate_only": True}
    if action.name == "capture":
        from ..templates.capture import capture
        return capture(load_config(action.config_path), Path(args["proof"]), Path(args["output"]),
                       template_id=args["template_id"])
    if action.name in {"sources", "analyze"}:
        from . import archive_analysis
        manifest = Path(args["manifest"])
        if action.name == "sources":
            before = _manifest_digest(manifest)
            sources = archive_analysis.read_sources(manifest)
            if _manifest_digest(manifest) != before:
                raise ValueError("交付清单已变化，请重新读取")
            return {"sources": sources, "manifest_sha256": before}
        if _manifest_digest(manifest) != args.get("manifest_sha256"):
            raise ValueError("交付清单已变化，请重新选择文档")
        if not isinstance(args.get("selected_ids"), list) or not args["selected_ids"]:
            raise ValueError("必须明确选择分析文档")
        return archive_analysis.execute(manifest, Path(args["output"]), config_path=action.config_path,
                                        use_ai=args.get("use_ai") is True, selected_ids=args["selected_ids"])
    if action.name in {"failures", "retry"}:
        from ..commands.recovery import execute as recover
        if action.name == "failures":
            return recover(str(action.config_path), "failures", limit=1000)
        if args.get("confirmed") is not True or not args.get("fingerprints"):
            raise ValueError("必须选择失败请求并确认重试")
        return recover(str(action.config_path), "retry-failed", fingerprints=args["fingerprints"])
    if action.name.startswith("repair:"):
        from .repair_workflow import execute as repair
        operation = action.name.partition(":")[2]
        if operation in {"apply", "rollback", "observe"} and args.get("confirmed") is not True:
            raise ValueError("修改配置前需要确认")
        if operation in {"preview", "apply"}:
            binding, payloads = repair_binding(Path(args["evidence"]), Path(args["candidate"]))
            if binding != args.get("preview_binding"):
                raise ValueError("证据或候选已变化，请重新预览")
            # Compare and publish from the exact reviewed bytes, even if the
            # original files are edited while the worker is running.
            with tempfile.TemporaryDirectory(prefix="omnicrawler-repair-") as folder:
                evidence, candidate = Path(folder) / "evidence.json", Path(folder) / "candidate.json"
                evidence.write_bytes(payloads[0])
                candidate.write_bytes(payloads[1])
                result = repair(operation, config_path=action.config_path, evidence=evidence, candidate_path=candidate)
            return {**result, "preview_binding": binding}
        return repair(operation, config_path=action.config_path,
                      evidence=Path(args["evidence"]) if args.get("evidence") else None,
                      candidate_path=Path(args["candidate"]) if args.get("candidate") else None)
    raise ValueError("不支持的任务工具操作")
