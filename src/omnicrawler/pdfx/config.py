from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from ..core.ai_env import bridge_pdfx_llm_env
from .templates import resolve_pdf_project_config

LOGGER = logging.getLogger(__name__)

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        def repl(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            # D6：环境变量存在（含空串）就用它；不存在才用默认；无默认保留字面量并告警
            if name in os.environ:
                return os.environ[name]
            if default is not None:
                return default
            LOGGER.warning("环境变量 %s 未设置且无默认值，保留字面量", name)
            return match.group(0)
        return _ENV_RE.sub(repl, value)
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    return value


@dataclass(slots=True)
class FieldSpec:
    name: str
    label: str
    description: str = ""
    type: str = "text"
    aliases: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)
    source: str = "content"
    required: bool = False
    target_unit: str | None = None
    value_aliases: dict[str, list[str]] = field(default_factory=dict)
    allowed_values: list[str] = field(default_factory=list)
    minimum: float | None = None
    maximum: float | None = None
    # 校验用白名单正则（D28：code 等字段的取值形态约束，与提取 patterns 分离）
    value_pattern: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> FieldSpec:
        if not raw.get("name") or not raw.get("label"):
            raise ValueError("每个字段必须设置 name 和 label")
        known = {
            "name", "label", "description", "type", "aliases", "patterns",
            "source", "required", "target_unit", "value_aliases",
            "allowed_values", "minimum", "maximum", "value_pattern",
        }
        unknown = set(raw) - known
        if unknown:
            raise ValueError(f"字段 {raw['name']} 存在未知配置项: {sorted(unknown)}")
        name = str(raw["name"])
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name):
            raise ValueError(
                f"字段名 {name!r} 无效；只能使用英文、数字、下划线且不能以数字开头"
            )
        if len(str(raw["label"])) > 100:
            raise ValueError(f"字段 {name} 的 label 超过100字符")
        patterns = raw.get("patterns", [])
        aliases = raw.get("aliases", [])
        if not isinstance(patterns, list) or len(patterns) > 100:
            raise ValueError(f"字段 {name} 的 patterns 必须是最多100项的列表")
        if not isinstance(aliases, list) or len(aliases) > 200:
            raise ValueError(f"字段 {name} 的 aliases 必须是最多200项的列表")
        if not all(isinstance(item, str) for item in patterns):
            raise ValueError(f"字段 {name} 的每个 pattern 都必须是字符串")
        if not all(isinstance(item, str) for item in aliases):
            raise ValueError(f"字段 {name} 的每个 alias 都必须是字符串")
        if raw.get("source", "content") not in {"content", "filename", "both"}:
            raise ValueError(f"字段 {name} 的 source 只能是 content、filename 或 both")
        if "required" in raw and not isinstance(raw["required"], bool):
            raise ValueError(f"字段 {name} 的 required 必须是 true 或 false")
        # D25：minimum/maximum 显式转 float（YAML 引号字符串会引发 float<str TypeError）
        minimum = raw.get("minimum")
        maximum = raw.get("maximum")
        if minimum not in {None, ""}:
            try:
                minimum = float(minimum)
            except (TypeError, ValueError):
                raise ValueError(f"字段 {name} 的 minimum 必须是数字")
        if maximum not in {None, ""}:
            try:
                maximum = float(maximum)
            except (TypeError, ValueError):
                raise ValueError(f"字段 {name} 的 maximum 必须是数字")
        # D26/D27：配置健全性——数值换算/枚举配置必须配对应 type，否则静默失效
        spec_type = str(raw.get("type", "text")).casefold()
        # S2.3.6/7：boolean/entity/relationship 接入白名单（normalization.py 对应分支已可达）
        allowed_types = {
            "text", "amount", "currency", "date", "percent", "integer", "number",
            "enum", "code", "year", "boolean", "entity", "relationship",
        }
        if spec_type not in allowed_types:
            raise ValueError(f"字段 {name} 的 type 不支持: {spec_type}")
        if raw.get("target_unit") and spec_type not in {"amount", "currency", "number", "integer", "percent"}:
            raise ValueError(f"字段 {name} 设置了 target_unit 但 type 不是数值类型（当前 {spec_type}）")
        if raw.get("allowed_values") and spec_type != "enum":
            raise ValueError(f"字段 {name} 设置了 allowed_values 但 type 不是 enum（当前 {spec_type}）")
        return cls(
            **{**raw, "minimum": minimum, "maximum": maximum, "type": spec_type},
        )

    @property
    def search_terms(self) -> list[str]:
        seen: set[str] = set()
        terms: list[str] = []
        for term in [self.label, *self.aliases]:
            cleaned = term.strip()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                terms.append(cleaned)
        return terms


@dataclass(slots=True)
class ProjectConfig:
    path: Path
    project_name: str
    input_dir: Path
    work_dir: Path
    output_dir: Path
    database: Path
    parser: dict[str, Any]
    ocr: dict[str, Any]
    retrieval: dict[str, Any]
    llm: dict[str, Any]
    extraction: dict[str, Any]
    normalization: dict[str, Any]
    validation: dict[str, Any]
    fields: list[FieldSpec]

    def field_map(self) -> dict[str, FieldSpec]:
        return {item.name: item for item in self.fields}


def _resolve_path(value: str | Path, base: Path) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _pdf_project_root(path: Path) -> Path | None:
    """按 pyproject.toml 上溯推断 PDF 项目根（供 AI 配置桥接定位项目 .env）。"""
    for candidate in [path.parent, *path.parents]:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return None


def load_config(config_path: str | Path) -> ProjectConfig:
    path = resolve_pdf_project_config(config_path)
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {path}")
    # 将 GUI 写入的 OMNICRAWL_AI_* 桥接为 PDFX_LLM_* 兼容别名，
    # 使 CLI/headless 路径与 GUI PDF 工作台行为一致（显式配置优先，不覆盖）。
    bridge_pdfx_llm_env(_pdf_project_root(path))
    raw = _expand_env(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    fields = [FieldSpec.from_dict(item) for item in raw.get("fields", [])]
    if not fields:
        raise ValueError("配置中至少需要一个 fields 字段")
    names = [item.name for item in fields]
    if len(names) != len(set(names)):
        raise ValueError("fields 中的 name 不得重复")

    # Support standalone projects and installed builtin templates.  Legacy
    # ``configs/pdf`` references are resolved before this point.
    base = path.parent
    project_root_found = False
    for candidate in [path.parent, *path.parents]:
        if (candidate / "pyproject.toml").is_file():
            base = candidate
            project_root_found = True
            break
    if not project_root_found:
        for candidate in path.parents:
            if candidate.name == "configs":
                base = candidate.parent
                break
    input_dir = _resolve_path(raw.get("input_dir", "data/pdfs"), base)
    work_dir = _resolve_path(raw.get("work_dir", "work"), base)
    output_dir = _resolve_path(raw.get("output_dir", "output"), base)
    database = _resolve_path(raw.get("database", str(work_dir / "pipeline.sqlite3")), base)

    return ProjectConfig(
        path=path,
        project_name=raw.get("project_name", path.stem),
        input_dir=input_dir,
        work_dir=work_dir,
        output_dir=output_dir,
        database=database,
        parser=raw.get("parser", {}),
        ocr=raw.get("ocr", {}),
        retrieval=raw.get("retrieval", {}),
        llm=raw.get("llm", {}),
        extraction=raw.get("extraction", {}),
        normalization=raw.get("normalization", {}),
        validation=raw.get("validation", {}),
        fields=fields,
    )


def validate_runtime_config(config: ProjectConfig) -> list[str]:
    warnings: list[str] = []
    backend = str(config.ocr.get("backend", "none")).lower()
    if backend not in {"none", "paddle", "tesseract"}:
        raise ValueError("ocr.backend 只能是 none、paddle 或 tesseract")
    provider = str(config.llm.get("provider", "disabled")).lower()
    if provider not in {"disabled", "openai_compatible"}:
        raise ValueError("llm.provider 只能是 disabled 或 openai_compatible")
    if provider != "disabled" and not config.llm.get("model"):
        raise ValueError("启用大模型时必须设置 llm.model")
    if provider != "disabled" and not config.llm.get("api_key"):
        warnings.append("大模型已启用，但 API Key 为空；extract 阶段会失败")
    if provider != "disabled":
        parsed = urlparse(str(config.llm.get("base_url", "")))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("llm.base_url 必须是有效的 HTTP(S) 地址")
    if backend == "none":
        warnings.append("OCR 未启用，扫描页会保留为待 OCR 状态")
    dpi = int(config.ocr.get("dpi", 220))
    if not 72 <= dpi <= 600:
        raise ValueError("ocr.dpi 必须在72到600之间")
    for section, key, default in (
        (config.parser, "workers", 4),
        (config.extraction, "workers", 4),
    ):
        workers = int(section.get(key, default))
        if not 1 <= workers <= 64:
            raise ValueError(f"{key} 必须在1到64之间")

    min_chars = int(config.parser.get("min_native_chars", 40))
    if not 0 <= min_chars <= 1_000_000:
        raise ValueError("parser.min_native_chars 必须在0到1000000之间")
    garbled = float(config.parser.get("max_garbled_ratio", 0.03))
    if not 0 <= garbled <= 1:
        raise ValueError("parser.max_garbled_ratio 必须在0到1之间")
    top_pages = int(config.retrieval.get("top_pages", 3))
    neighbors = int(config.retrieval.get("neighbor_pages", 1))
    if not 1 <= top_pages <= 1000:
        raise ValueError("retrieval.top_pages 必须在1到1000之间")
    if not 0 <= neighbors <= 100:
        raise ValueError("retrieval.neighbor_pages 必须在0到100之间")
    max_chars = int(config.extraction.get("max_chars_per_page", 12000))
    if not 100 <= max_chars <= 2_000_000:
        raise ValueError("extraction.max_chars_per_page 必须在100到2000000之间")
    confidence = float(config.validation.get("auto_accept_confidence", 0.9))
    if not 0 <= confidence <= 1:
        raise ValueError("validation.auto_accept_confidence 必须在0到1之间")
    if provider != "disabled":
        timeout = float(config.llm.get("timeout_seconds", 180))
        attempts = int(config.llm.get("retry_attempts", 4))
        tokens = int(config.llm.get("max_output_tokens", 4000))
        if not 1 <= timeout <= 3600:
            raise ValueError("llm.timeout_seconds 必须在1到3600之间")
        if not 1 <= attempts <= 20:
            raise ValueError("llm.retry_attempts 必须在1到20之间")
        if not 1 <= tokens <= 1_000_000:
            raise ValueError("llm.max_output_tokens 必须在1到1000000之间")
        temperature = float(config.llm.get("temperature", 0))
        if not -2 <= temperature <= 2:
            raise ValueError("llm.temperature 必须在-2到2之间")
        if config.llm.get("include_page_images", False):
            image_dpi = int(config.llm.get("image_dpi", 144))
            if not 72 <= image_dpi <= 300:
                raise ValueError("llm.image_dpi 必须在72到300之间")

    def overlaps(left: Path, right: Path) -> bool:
        try:
            left.relative_to(right)
            return True
        except ValueError:
            try:
                right.relative_to(left)
                return True
            except ValueError:
                return False

    if overlaps(config.input_dir, config.work_dir):
        raise ValueError("PDF 输入目录不能与工作目录重叠")
    if overlaps(config.input_dir, config.output_dir):
        raise ValueError("PDF 输入目录不能与输出目录重叠")
    if overlaps(config.database, config.input_dir):
        raise ValueError("数据库不能放在 PDF 输入目录内")
    return warnings
