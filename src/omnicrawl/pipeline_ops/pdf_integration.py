from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..core.config import AppConfig, resolve_pdf_template
from ..state import StateStore
from .provenance import write_pdf_source_manifest

EventCallback = Callable[[str, dict[str, Any]], None]
StopCallback = Callable[[], bool]


def _pdf_input_dir(config: AppConfig) -> Path:
    """S2.3.5：PDF 附件输入目录——显式配置 storage.objects.local_directory（非默认 "."）时
    以其驱动，不再被硬编码 artifacts/pdf 绕过。"""
    objects = config.section("storage").get("objects", {})
    local_dir = str(objects.get("local_directory", "")).strip() if isinstance(objects, dict) else ""
    if local_dir and local_dir != ".":
        return (config.workspace / local_dir / "pdf").resolve()
    return (config.workspace / "artifacts" / "pdf").resolve()


def ensure_pdf_project(config: AppConfig) -> tuple[Path, bool]:
    """创建一份持久化的 PDF 工作台配置，之后可独立调整和续跑。"""
    from ..pdfx.project import create_project_config

    settings = config.section("processors").get("pdf", {})
    configured = str(settings.get("project_config", "")).strip()
    project_path = (
        config.resolve(configured)
        if configured
        else (config.workspace / "pdf" / "project.yaml").resolve()
    )
    if project_path.is_file():
        return project_path, False
    if configured:
        raise FileNotFoundError(f"PDF项目配置不存在: {project_path}")

    template = resolve_pdf_template(config, settings.get("config", ""))
    pdf_input = _pdf_input_dir(config)
    work = config.workspace / "pdf" / "work"
    output = config.workspace / "output" / "pdf"
    project_path.parent.mkdir(parents=True, exist_ok=True)
    path = create_project_config(
        template,
        project_path,
        project_name=f"{config.project_name}-PDF",
        input_dir=pdf_input,
        work_dir=work,
        output_dir=output,
        ocr_backend=str(
            settings.get("ocr_backend") or os.environ.get("PDFX_OCR_BACKEND", "none")
        ),
    )
    return path, True


def run_pdf_pipeline(
    config: AppConfig,
    state: StateStore,
    *,
    run_id: str | None = None,
    callback: EventCallback | None = None,
    should_stop: StopCallback | None = None,
) -> dict[str, Any]:
    try:
        from ..pdfx.service import run_extraction
    except ImportError as exc:
        raise RuntimeError(
            "PDF处理依赖未安装；请执行 pip install -e '.[pdf]'"
        ) from exc

    settings = config.section("processors").get("pdf", {})
    pdf_input = _pdf_input_dir(config)
    manifest = write_pdf_source_manifest(config.workspace, state, run_id=run_id)
    # S2.3.5：子目录 PDF 也计入（rglob 递归），不再只扫顶层
    pdf_files = list(pdf_input.rglob("*.pdf")) if pdf_input.exists() else []
    if not pdf_files:
        return {
            "enabled": True,
            "documents": 0,
            "source_manifest": manifest,
            "message": "未发现PDF附件",
        }

    project_path, created = ensure_pdf_project(config)
    result = run_extraction(
        project_path,
        auto_prepare=True,
        run_ocr=not settings.get("skip_ocr", False),
        callback=callback,
        should_stop=should_stop,
    )
    return {
        "enabled": True,
        "documents": len(pdf_files),
        "project_config": str(project_path),
        "project_created": created,
        "source_manifest": manifest,
        "result": result,
    }
