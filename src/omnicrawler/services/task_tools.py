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


def execute(action: TaskAction) -> dict[str, Any]:
    if hashlib.sha256(action.config_path.read_bytes()).hexdigest() != action.config_sha256:
        raise ValueError("已保存配置已变化，请重新打开任务工具")
    args = action.arguments
    if action.name == "capture":
        from ..templates.capture import capture
        return capture(load_config(action.config_path), Path(args["proof"]), Path(args["output"]),
                       template_id=args["template_id"])
    if action.name in {"sources", "analyze"}:
        from . import archive_analysis
        manifest = Path(args["manifest"])
        if action.name == "sources":
            before = hashlib.sha256(manifest.read_bytes()).hexdigest()
            sources = archive_analysis.read_sources(manifest)
            if hashlib.sha256(manifest.read_bytes()).hexdigest() != before:
                raise ValueError("交付清单已变化，请重新读取")
            return {"sources": sources, "manifest_sha256": before}
        if hashlib.sha256(manifest.read_bytes()).hexdigest() != args.get("manifest_sha256"):
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
