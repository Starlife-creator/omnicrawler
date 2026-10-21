"""ConvertX 具名配方（preset）：把常用选项、输出策略与格式参数收成一份 JSON。

兼容策略（对齐成熟工具的 lenient enum）：
- **未知键忽略并记警告**，不让整份配置解析失败；
- **未知枚举降级为默认值并记警告**（例如 compression 写成 ``"ultra"`` → zstd）；
- 用户文件同名覆盖内置配方；JSON 语法错误仍应报错（那是文件本身不可用）。

不涉及网络、不下载任何东西。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .output_rules import CONFLICT_POLICIES

_PRESET_VERSION = 1
_ALLOWED_CONVERT_KEYS: frozenset[str] = frozenset(
    {"flat", "nested", "table", "compression", "on_error"}
)
_ALLOWED_OUTPUT_KEYS: frozenset[str] = frozenset(
    {"directory", "template", "on_conflict", "preserve_timestamps", "sanitize"}
)
_ALLOWED_COMPRESSION: frozenset[str] = frozenset({"zstd", "snappy", "gzip", "none"})
_ALLOWED_ON_ERROR: frozenset[str] = frozenset({"skip", "abort"})


@dataclass(frozen=True, slots=True)
class Preset:
    name: str
    description: str = ""
    options: dict[str, Any] = field(default_factory=dict)
    convert_kwargs: dict[str, Any] = field(default_factory=dict)
    output_rule: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def merged_options(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        merged = dict(self.options)
        merged.update(overrides or {})
        return merged

    def merged_convert_kwargs(self, explicit: dict[str, Any] | None = None) -> dict[str, Any]:
        merged = dict(self.convert_kwargs)
        merged.update({key: value for key, value in (explicit or {}).items() if value is not None})
        return merged


def builtin_presets() -> dict[str, Preset]:
    return {
        "table-archive": Preset(
            name="table-archive",
            description="表格归档：Parquet + zstd，重名自动改名，保留时间戳",
            convert_kwargs={"compression": "zstd"},
            output_rule={"on_conflict": "rename", "preserve_timestamps": True},
        ),
        "table-share": Preset(
            name="table-share",
            description="表格分发：XLSX/CSV 常用设置，重名跳过",
            output_rule={"on_conflict": "skip"},
        ),
        "document-web": Preset(
            name="document-web",
            description="文档发布：HTML 输出，重名自动改名",
            output_rule={"on_conflict": "rename", "preserve_timestamps": True},
        ),
        "image-web": Preset(
            name="image-web",
            description="图片发布：质量 82、优化编码、去元数据、重名自动改名",
            options={"quality": 82, "optimize": True, "strip_metadata": True},
            output_rule={"on_conflict": "rename"},
        ),
    }


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"无法读取 preset 文件 {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"preset 文件 JSON 解析失败 {path}: {exc}") from exc


def _coerce_preset(name: str, raw: Any) -> Preset:
    warnings: list[str] = []
    if not isinstance(raw, dict):
        return Preset(name=name, warnings=(f"preset {name!r} 不是对象，已忽略",))

    description = str(raw.get("description") or "")
    raw_options = raw.get("options", {})
    if not isinstance(raw_options, dict):
        warnings.append(f"preset {name!r}: options 不是对象，已忽略")
        raw_options = {}

    raw_kwargs = raw.get("convert", raw.get("convert_kwargs", {}))
    if not isinstance(raw_kwargs, dict):
        warnings.append(f"preset {name!r}: convert 不是对象，已忽略")
        raw_kwargs = {}
    convert_kwargs: dict[str, Any] = {}
    for key, value in raw_kwargs.items():
        if key not in _ALLOWED_CONVERT_KEYS:
            warnings.append(f"preset {name!r}: 未知 convert 键 {key!r} 已忽略")
            continue
        if key == "compression":
            text = str(value).lower()
            if text not in _ALLOWED_COMPRESSION:
                warnings.append(
                    f"preset {name!r}: 未知 compression {value!r}，降级为 zstd"
                )
                text = "zstd"
            convert_kwargs[key] = text
        elif key == "on_error":
            text = str(value).lower()
            if text not in _ALLOWED_ON_ERROR:
                warnings.append(f"preset {name!r}: 未知 on_error {value!r}，降级为 skip")
                text = "skip"
            convert_kwargs[key] = text
        elif key == "table":
            convert_kwargs[key] = str(value)
        else:
            convert_kwargs[key] = bool(value)

    raw_output = raw.get("output", raw.get("output_rule", {}))
    if not isinstance(raw_output, dict):
        warnings.append(f"preset {name!r}: output 不是对象，已忽略")
        raw_output = {}
    output_rule: dict[str, Any] = {}
    for key, value in raw_output.items():
        if key not in _ALLOWED_OUTPUT_KEYS:
            warnings.append(f"preset {name!r}: 未知 output 键 {key!r} 已忽略")
            continue
        if key == "on_conflict":
            text = str(value).lower()
            if text not in CONFLICT_POLICIES:
                warnings.append(
                    f"preset {name!r}: 未知 on_conflict {value!r}，降级为 overwrite"
                )
                text = "overwrite"
            output_rule[key] = text
        elif key in {"preserve_timestamps", "sanitize"}:
            output_rule[key] = bool(value)
        elif key == "directory":
            output_rule[key] = str(value)
        else:
            output_rule[key] = str(value)

    return Preset(
        name=name,
        description=description,
        options=dict(raw_options),
        convert_kwargs=convert_kwargs,
        output_rule=output_rule,
        warnings=tuple(warnings),
    )


def load_presets(path: Path | str) -> dict[str, Preset]:
    """读取 preset 文件；接受 ``{"presets": {...}}`` 或直接 ``{name: {...}}``。"""
    payload = _load_json(Path(path))
    if not isinstance(payload, dict):
        raise ValueError("preset 文件顶层必须是对象")
    raw_presets = payload.get("presets", payload)
    if not isinstance(raw_presets, dict):
        raise ValueError("preset 文件的 presets 必须是对象")
    return {str(name): _coerce_preset(str(name), raw) for name, raw in raw_presets.items()}


def available_presets(path: Path | str | None = None) -> dict[str, Preset]:
    presets = builtin_presets()
    if path is not None:
        presets.update(load_presets(path))
    return presets


def get_preset(name: str, path: Path | str | None = None) -> Preset:
    presets = available_presets(path)
    if name not in presets:
        options = ", ".join(sorted(presets))
        raise KeyError(f"未知 preset {name!r}；可用: {options}")
    return presets[name]


def write_example_presets(path: Path) -> None:
    payload = {
        "version": _PRESET_VERSION,
        "presets": {
            name: {
                "description": preset.description,
                "options": preset.options,
                "convert": preset.convert_kwargs,
                "output": preset.output_rule,
            }
            for name, preset in builtin_presets().items()
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


__all__ = [
    "Preset",
    "available_presets",
    "builtin_presets",
    "get_preset",
    "load_presets",
    "write_example_presets",
]
