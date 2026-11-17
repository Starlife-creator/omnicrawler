"""Semantic dependencies of extraction from already parsed PDF pages."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .config import ProjectConfig
from .normalization import EntityResolver


def extraction_digest(config: ProjectConfig, resolver: EntityResolver) -> str:
    payload = {
        "contract": 1,
        "fields": [asdict(field) for field in config.fields],
        "retrieval": config.retrieval,
        "extraction": {key: value for key, value in config.extraction.items() if key != "workers"},
        "normalization": config.normalization,
        "validation": config.validation,
        "llm": config.llm,
        "entity_aliases": resolver.aliases,
    }
    return _digest(payload)


def _digest(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                    default=str).encode("utf-8")).hexdigest()


def _file_identity(value: Any, base: Path) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return {"configured": False}
    path = Path(value).expanduser()
    path = path if path.is_absolute() else base / path
    try:
        if path.is_symlink() or not path.is_file():
            return {"configured": True, "path": str(path), "identity": "missing"}
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                size += len(block)
                digest.update(block)
        return {"configured": True, "path": str(path.resolve()),
                "size": size, "sha256": digest.hexdigest()}
    except OSError as exc:
        return {"configured": True, "path": str(path), "identity": type(exc).__name__}


def parser_digest(config: ProjectConfig, parser_version: str) -> str:
    return _digest({"contract": 1, "parser_version": parser_version,
                    "parser": {key: value for key, value in config.parser.items() if key != "workers"}})


def _tessdata_identities(config: ProjectConfig, base: Path) -> dict[str, Any]:
    """Hash the selected traineddata files, not an unversioned directory name.

    Tesseract's language models affect output independently of the executable.
    Only the languages explicitly selected by the task participate: unrelated
    installed models do not invalidate otherwise reusable OCR results.
    """
    ocr = config.ocr
    if str(ocr.get("backend", "tesseract")).lower() != "tesseract":
        return {"configured": False}
    directory = Path(os.environ.get("TESSDATA_PREFIX", "")).expanduser() if os.environ.get("TESSDATA_PREFIX") else None
    if directory is None:
        candidates = [base / "tessdata"]
        command = str(ocr.get("command") or os.environ.get("TESSERACT_CMD", ""))
        if command:
            command_path = Path(command).expanduser()
            command_path = command_path if command_path.is_absolute() else base / command_path
            candidates.extend([command_path.parent / "tessdata", command_path.parent.parent / "share" / "tessdata"])
        directory = next((path for path in candidates if path.is_dir()), candidates[0])
    languages = [language.strip() for language in str(ocr.get("lang", "eng")).split("+") if language.strip()]
    return {"configured": True, "languages": languages,
            "files": {language: _file_identity(str(directory / f"{language}.traineddata"), base)
                      for language in languages}}


def _paddle_cache_root() -> Path | None:
    """Paddle 模型缓存根目录（读 PaddleX 自己的环境变量）。

    与 ``services/model_download.cache_root()`` 同口径：优先
    ``PADDLE_PDX_CACHE_HOME``，否则 PaddleX 的默认位置。
    """
    raw = os.environ.get("PADDLE_PDX_CACHE_HOME", "")
    if raw:
        return Path(raw).expanduser()
    return None


def _official_models_dir(root: Path) -> Path:
    """模型目录：兼容 ``<root>/official_models`` 与 ``<root>`` 两种布局。"""
    nested = root / "official_models"
    return nested if nested.is_dir() else root


#: 能力开关 → 该开关需要的模型名（对应 `PPStructureV3` 的构造参数）。
#:
#: 这是**按需计算**的基础：只把真正启用的能力对应模型纳入指纹。
#: 装了但没启用（例如关掉表格识别却装了表格模型）**不该**让结果失效——
#: 否则用户每装一个组件就要全量重跑，代价在 1 GB 级缓存上不可接受。
_PADDLE_MODELS_BY_SWITCH: dict[str, tuple[str, ...]] = {
    # 文字识别是 OCR 的基本盘，任何后端启用都会用到
    "text": ("PP-OCRv5_server_det", "PP-OCRv5_server_rec"),
    "table_recognition": (
        "SLANeXt_wired",
        "SLANet_plus",
        "RT-DETR-L_wired_table_cell_det",
        "RT-DETR-L_wireless_table_cell_det",
        "PP-DocLayout_plus-L",
        "PP-DocBlockLayout",
    ),
    "orientation": ("PP-LCNet_x1_0_doc_ori",),
    "textline_orientation": ("PP-LCNet_x1_0_textline_ori",),
    "table_cell": ("PP-LCNet_x1_0_table_cls",),
}


def _enabled_paddle_switches(ocr: dict[str, Any]) -> set[str]:
    """本次配置实际启用了哪些能力（与 ``PPStructureV3`` 的默认值保持一致）。"""
    enabled = {"text"}  # 文字识别总是需要
    if bool(ocr.get("table_recognition", True)):
        enabled.add("table_recognition")
        enabled.add("table_cell")
    if bool(ocr.get("orientation", True)):
        enabled.add("orientation")
    if bool(ocr.get("textline_orientation", True)):
        enabled.add("textline_orientation")
    return enabled


def _bounded_file_identity(path: Path) -> dict[str, Any]:
    """**有界**的文件身份：只取大小与 mtime，不读内容。

    为什么不用内容哈希：模型目录是 1 GB 级（本地实测 11 个模型共 1037.8 MiB）。
    每次起任务都逐字节重算会让 OCR 启动变得很慢，而指纹只需要"能否察觉变化"。
    ``(size, mtime_ns)`` 对"换了模型文件"这一场景足够敏感，且代价恒定。
    """
    try:
        stat = path.stat()
    except OSError as exc:
        return {"identity": type(exc).__name__}
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _paddle_model_identities(config: ProjectConfig, base: Path) -> dict[str, Any]:
    """本次启用的 Paddle 模型身份（条目 8）。

    覆盖 ``ocr_digest`` 此前完全缺失的一类：Paddle 隐式管理的模型缓存。

    **为什么只算启用的模型**：见 ``_PADDLE_MODELS_BY_SWITCH`` 的注释。
    **为什么是有界身份**：见 ``_bounded_file_identity`` 的注释。

    未启用 paddle 时返回 ``{"configured": False}`` 且**不碰磁盘**——
    无关任务不该为模型目录付 IO。
    """
    ocr = config.ocr
    if str(ocr.get("backend", "none")).lower() != "paddle" and not ocr.get("component"):
        return {"configured": False}

    root = _paddle_cache_root()
    if root is None or not root.is_dir():
        # 读不到缓存根：明确记为"未知"，不假装算过。
        # 这比给一个恒定值更诚实——恒定值会让"配置变了但指纹没变"看起来正常。
        return {"configured": True, "cache_root": None, "identity": "unknown"}

    official = _official_models_dir(root)
    enabled = _enabled_paddle_switches(ocr)
    wanted: list[str] = []
    for switch in sorted(enabled):
        wanted.extend(_PADDLE_MODELS_BY_SWITCH.get(switch, ()))

    models: dict[str, Any] = {}
    for name in sorted(set(wanted)):
        directory = official / name
        weights = directory / "inference.pdiparams"
        if not weights.is_file():
            # 缺失也要记进指纹：从"有"变成"没有"同样影响结果
            models[name] = {"present": False}
            continue
        models[name] = {"present": True, **_bounded_file_identity(weights)}

    return {
        "configured": True,
        "cache_root": str(official),
        "switches": sorted(enabled),
        "models": models,
        # 兼容既有字段名（capabilities 的 F41 判据同样要求 inference.pdiparams）
        "missing": sorted(name for name, info in models.items() if not info.get("present")),
    }


#: OCR 指纹契约版本。**改动语义时必须升级**：
#: * 1 → 2（条目 8）：补入 Paddle 模型身份（此前完全缺失）。
#:
#: 升级的意义是让 `refresh_processing` 能识别"这是旧版本算出来的指纹"，
#: 从而对它做**一次性基线**处理，而不是把所有历史文档当成"依赖变了"全量重跑。
OCR_DIGEST_CONTRACT = 2


def ocr_digest(config: ProjectConfig, component_version: str | None = None) -> str:
    ocr = {key: value for key, value in config.ocr.items() if not key.startswith("_")}
    component_version = component_version or config.ocr.get("_resolved_component_version")
    command = ocr.get("command") or os.environ.get("TESSERACT_CMD", "")
    config_path = getattr(config, "path", None)
    base = Path(config_path).parent if config_path is not None else Path.cwd()
    identity = {
        "component_version": component_version,
        "command": _file_identity(command, base),
        "tessdata": _tessdata_identities(config, base),
        # 条目 8：补上此前完全缺失的 Paddle 模型身份。
        "paddle_models": _paddle_model_identities(config, base),
    }
    return _digest({"contract": OCR_DIGEST_CONTRACT, "ocr": ocr, "runtime": identity})
