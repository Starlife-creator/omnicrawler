"""Capture a reviewed public crawl as a bounded, credential-free reusable template."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml

from .._version import __version__
from ..core.config import AppConfig
from ..core.utils import atomic_write, utcnow
from .parameters import validate_parameters
from .template_catalog import TemplateMetadata, TemplateRecord
from .template_health import validate_template


def config_digest(config: AppConfig) -> str:
    return hashlib.sha256(json.dumps(config.raw, sort_keys=True, default=str).encode()).hexdigest()


def trial_reference(config: AppConfig, result: dict[str, Any], samples: list[dict[str, Any]],
                    components: list[dict[str, Any]], *, components_consistent: bool = True) -> dict[str, Any]:
    """A machine trial is a historical reference, never a transferable approval."""
    return {"format": 1, "config_sha256": config_digest(config), "captured_at": utcnow(),
            "run_id": str(result.get("run_id", "")), "samples": samples,
            "summary": {key: result.get(key) for key in ("status", "processed", "records", "failed")},
            "versions": {"core": __version__, "plugins": result.get("plugins", {}).get("plugins", []),
                         "components_consistent": components_consistent,
                         "components": [{"name": item["name"], "version": item["version"]} for item in components]},
            "historical_reference_only": True}


def capture(config: AppConfig, proof_path: Path, output: Path, *, template_id: str,
            parameter_path: Path | None = None, force: bool = False) -> dict[str, Any]:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._/-]*", template_id) or ".." in template_id:
        raise ValueError("模板 id 需要稳定的小写标识")
    if proof_path.stat().st_size > 1024**2:
        raise ValueError("试跑摘要过大")
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    if not isinstance(proof, dict) or proof.get("format") != 1 or proof.get("config_sha256") != config_digest(config):
        raise ValueError("试跑摘要缺少当前配置绑定，请重新 sample")
    summary = proof.get("summary", {})
    samples = proof.get("samples", [])
    if not isinstance(proof.get("versions"), dict) or proof["versions"].get("components_consistent") is not True:
        raise ValueError("试跑期间组件版本不一致或旧摘要缺少版本绑定，请重新 sample")
    if not isinstance(summary, dict) or summary.get("status") != "succeeded" or not summary.get("processed") or not samples:
        raise ValueError("缺少完整成功试跑和来源快照")
    if not isinstance(samples, list) or any(not isinstance(item, dict) or
        not re.fullmatch(r"[0-9a-f]{64}", str(item.get("content_sha256", ""))) or
        not re.fullmatch(r"[0-9a-f]{64}", str(item.get("request_fingerprint", ""))) for item in samples):
        raise ValueError("来源快照摘要格式无效")
    source = config.section("source")
    if source.get("kind") not in {"static_html", "url_list", "crawl", "focused", "incremental"}:
        raise ValueError("首期仅捕获公开 HTTP 页面任务；登录/API/插件任务使用本地配置历史")
    if source.get("pagination") or source.get("auth_check") or config.section("extract").get("mode") == "json":
        raise ValueError("首期捕获简单 HTML seeds 任务；分页、认证和 JSON 任务使用原始配置历史")
    if any(config.section("crawl").get(key) for key in ("allow_patterns", "deny_patterns")):
        raise ValueError("首期不捕获带自定义 URL 过滤器的任务，请保留原始配置")
    if config.section("download").get("enabled"):
        raise ValueError("首期捕获页面记录任务；附件下载任务请保留原配置及交付清单")
    # Deliberately allowlist the public scenario instead of guessing whether arbitrary
    # headers, bodies, plugin fields, database URLs or unknown keys contain secrets.
    data: dict[str, Any] = {"config_version": 5,
        "project": {"name": "reused_public_task", "workspace": "work/reused_public_task"},
        "source": {"kind": source["kind"], "seeds": copy.deepcopy(source.get("seeds", []))},
        "crawl": {key: value for key, value in config.section("crawl").items()
                  if key in {"max_pages", "max_depth", "same_host", "concurrency", "strategy"}},
        "http": {key: value for key, value in config.section("http").items()
                 if key in {"respect_robots", "delay_seconds", "timeout_seconds"}},
        "egress": {key: value for key, value in config.section("egress").items()
                   if key in {"allowed_schemes", "allowed_ports", "allowed_domains", "maximum_requests",
                              "maximum_bytes", "maximum_concurrency", "maximum_runtime_seconds", "maximum_cost"}},
        "extract": _public_extract(config.section("extract")),
        "outputs": {key: bool(config.section("outputs").get(key)) for key in ("jsonl", "csv", "xlsx")}}
    data["egress"].update(enabled=True, audit=True)
    if not data["source"]["seeds"]:
        raise ValueError("公开任务需要 seeds")
    specifications = {}
    if parameter_path is not None:
        if parameter_path.stat().st_size > 65536:
            raise ValueError("参数声明过大")
        specifications = json.loads(parameter_path.read_text(encoding="utf-8"))
    if not isinstance(specifications, dict) or len(specifications) > 30:
        raise ValueError("参数声明需要对象且至多 30 项")
    declarations = {}
    used_paths = set()
    for name, specification in specifications.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or not isinstance(specification, dict):
            raise ValueError("参数名或声明无效")
        location = specification.get("path", "")
        if not isinstance(location, str) or not re.fullmatch(
                r"(?:source\.seeds\.\d+|crawl\.(?:max_pages|max_depth|concurrency)|http\.(?:timeout_seconds|delay_seconds)|extract\.fields\.[A-Za-z_][A-Za-z0-9_]*\.(?:selector|xpath))", location):
            raise ValueError("参数路径不属于可分享字段")
        if location in used_paths:
            raise ValueError("参数路径重复")
        used_paths.add(location)
        spec = {key: value for key, value in specification.items() if key != "path"}
        if spec.get("type") not in {"string", "integer", "number"}:
            raise ValueError("参数需要明确 string/integer/number 类型")
        if location.startswith("source.seeds."):
            spec = {"type": "string", "required": True}  # never copy URL credentials or signed query defaults
        parent, key = _location(data, location)
        original = parent[int(key)] if isinstance(parent, list) else parent[key]
        validate_parameters({name: spec}, {name: original}, strict=True)
        if isinstance(parent, list):
            parent[int(key)] = "{{" + name + "}}"
        else:
            parent[key] = "{{" + name + "}}"
        declarations[name] = spec
    for index in range(len(data["source"]["seeds"])):
        if f"source.seeds.{index}" in used_paths:
            continue
        name = f"seed_url_{index + 1}"
        if name in declarations:
            raise ValueError("自动网址参数与声明重名")
        declarations[name] = {"type": "string", "required": True}
        data["source"]["seeds"][index] = "{{" + name + "}}"
    # Reconstruct reference metadata; never serialize arbitrary proof fields or errors.
    safe_reference = {"format": 1, "config_sha256": config_digest(config),
        "captured_at": str(proof.get("captured_at", "")), "run_id": str(proof.get("run_id", "")),
        "samples": [{key: item.get(key) for key in ("request_fingerprint", "content_sha256", "fetched_at")} for item in samples],
        "summary": {key: summary.get(key) for key in ("status", "processed", "records", "failed")},
        "versions": _safe_versions(proof.get("versions", {})), "historical_reference_only": True}
    data["template_version"] = 1
    data["template"] = {"id": template_id, "name": template_id, "category": "user/public-http",
        "version": "1.0.0", "description": "由已试跑公开 HTTP 配置捕获；新参数须正常验证与试跑",
        "capabilities": ["http"], "placeholders": declarations,
        "verified_at": safe_reference["captured_at"], "acceptance_reference": safe_reference,
        "limitations": "仅公开 HTTP 范围；移除登录、代理、请求正文、插件、数据库及未知设置；不恢复运行批准"}
    output = output.expanduser().resolve()
    if output.exists() and not force:
        raise FileExistsError("目标已存在；使用 --force 授权覆盖")
    record = TemplateRecord(TemplateMetadata(template_id, template_id, "user/public-http",
                            description=data["template"]["description"], capabilities=("http",),
                            placeholders=declarations, verified_at=str(safe_reference["captured_at"])), output, data, False)
    health = validate_template(record)
    if not health.ok:
        raise ValueError("捕获模板不满足契约: " + "; ".join(health.errors))
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        from ..services.config_history import ConfigHistory
        ConfigHistory(config.root / ".config_history").snapshot(output, reason="before_template_capture")
    atomic_write(output, yaml.safe_dump(data, allow_unicode=True, sort_keys=False).encode())
    return {"created": str(output), "template_id": template_id, "historical_reference_only": True,
            "next": "templates render → validate → plan → sample; historical trial never approves a new task"}


def _public_extract(section: dict[str, Any]) -> dict[str, Any]:
    allowed = {"selector", "xpath", "type", "attr", "all", "required", "regex", "group", "join"}
    if any(section.get(key) for key in ("parser", "extractor", "deduplicate_by", "enrich", "scene", "processor_options", "parser_options", "extractor_options")):
        raise ValueError("首期不捕获自定义提取扩展；请保留原始配置")
    fields = section.get("fields", {})
    normalized = {}
    for name, rule in fields.items():
        if isinstance(rule, str):
            rule = {"selector": rule}
        if not isinstance(rule, dict) or set(rule) - allowed:
            raise ValueError("提取规则包含首期未支持的属性，不能静默移除")
        normalized[name] = copy.deepcopy(rule)
    return {"mode": section.get("mode", "auto"), "item_selector": section.get("item_selector", ""),
            "fields": normalized, "quality_threshold": section.get("quality_threshold", 0.8),
            "review_low_confidence": True}


def _location(data: dict[str, Any], location: str) -> tuple[Any, str]:
    parts = location.split(".")
    parent: Any = data
    try:
        for part in parts[:-1]:
            parent = parent[int(part)] if isinstance(parent, list) else parent[part]
        key = parts[-1]
        _ = parent[int(key)] if isinstance(parent, list) else parent[key]
        return parent, key
    except (IndexError, KeyError, ValueError, TypeError):
        raise ValueError("参数路径不在捕获配置中") from None


def _safe_versions(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"status": "unknown"}
    return {"core": str(value.get("core", "unknown")),
            "components_consistent": value.get("components_consistent") is True,
            "plugins": [str(item) for item in value.get("plugins", []) if isinstance(item, str)],
            "components": [{"name": str(item.get("name", "")), "version": str(item.get("version", ""))}
                           for item in value.get("components", []) if isinstance(item, dict)]}
