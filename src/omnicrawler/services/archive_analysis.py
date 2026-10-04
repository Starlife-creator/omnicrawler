"""Selected, checksum-verified archives to local evidence and optional interpretations."""
from __future__ import annotations

import hashlib
import json
from itertools import zip_longest
from pathlib import Path
from typing import Any

from ..core.config import load_config
from ..core.utils import atomic_write
from ..document_ir import parse_document
from ..security.egress import EgressBroker
from .ai_providers import build_provider
from .ai_safety import AIBudget, mark_untrusted, validate_ai_output


def _json(path: Path) -> dict[str, Any]:
    if path.stat().st_size > 2 * 1024**2:
        raise ValueError("分析清单或阶段文件超过大小限制")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("分析输入必须是 JSON 对象")
    return value


def read_sources(manifest: Path) -> list[dict[str, Any]]:
    """Read bounded selection metadata; content hashes are verified only for selected inputs."""
    if manifest.suffix.lower() != ".jsonl":
        document = _json(manifest)
        if document.get("format") != 1 or not isinstance(document.get("sources"), list):
            raise ValueError("需要格式 1 交付清单")
        entries = document["sources"]
    else:
        if manifest.stat().st_size > 2 * 1024**2:
            raise ValueError("交付清单超过大小限制")
        entries = []
        root = manifest.resolve().parent
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            if not isinstance(item, dict) or item.get("verification") != "verified_file":
                raise ValueError("PDF 交付清单缺少文件核验标记")
            path = Path(str(item.get("file_path", "")))
            if root not in path.resolve().parents:
                raise ValueError("交付文件越出清单目录")
            relative = str(path.relative_to(root))
            entries.append({"id": hashlib.sha256(relative.encode()).hexdigest(), "path": relative,
                            "sha256": item.get("sha256"), "source_url": item.get("source_url", "")})
    if not 1 <= len(entries) <= 1000 or any(not isinstance(entry, dict) for entry in entries):
        raise ValueError("交付清单需要 1–1000 份文档")
    ids = [entry.get("id") for entry in entries]
    if any(not isinstance(identity, str) or not identity for identity in ids) or len(set(ids)) != len(ids):
        raise ValueError("交付文档 id 缺失或重复")
    return entries


def _verified_sources(manifest: Path, selected_ids: list[str] | None = None) -> list[dict[str, Any]]:
    entries = read_sources(manifest)
    if selected_ids is not None:
        if not selected_ids or len(set(selected_ids)) != len(selected_ids) or not set(selected_ids) <= {entry["id"] for entry in entries}:
            raise ValueError("需要明确且有效的非空文档选择")
        entries = [entry for entry in entries if entry["id"] in selected_ids]
    if not 1 <= len(entries) <= 20:
        raise ValueError("格式 1 分析清单需要 1–20 份明确选择的交付文档")
    result = []
    total_bytes = 0
    root = manifest.resolve().parent
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"]:
            raise ValueError("文档需要稳定 id")
        if not isinstance(entry.get("path"), str) or not entry["path"]:
            raise ValueError("文档需要相对文件路径")
        relative = Path(entry["path"])
        path = root / relative
        if relative.is_absolute() or root not in path.resolve().parents:
            raise ValueError("分析文档越出清单目录或使用链接")
        if any(part.is_symlink() or getattr(part, "is_junction", lambda: False)()
               for part in (path, *path.parents) if part != root and root in part.parents):
            raise ValueError("分析文档不能使用链接")
        path = path.resolve()
        if not path.is_file() or path.stat().st_size > 20 * 1024**2:
            raise ValueError("分析文档超过大小限制")
        total_bytes += path.stat().st_size
        if total_bytes > 40 * 1024**2:
            raise ValueError("所选分析文档合计超过 40 MiB，请缩小输入")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != entry.get("sha256"):
            raise ValueError("分析文档与已验证交付哈希不一致")
        result.append({"id": entry["id"], "path": str(path), "sha256": digest,
                       "source_url": str(entry.get("source_url", ""))})
    if len({entry["id"] for entry in result}) != len(result):
        raise ValueError("分析文档 id 重复")
    return result


def _facts(sources: list[dict[str, Any]]) -> dict[str, Any]:
    documents = []
    evidence: list[dict[str, Any]] = []
    characters = 0
    for source in sources:
        document_characters = 0
        document_evidence = 0
        parsed = parse_document(source["path"])
        locators = getattr(parsed, "paragraph_locators", [])
        if locators and len(locators) != len(parsed.paragraphs):
            raise ValueError("文档段落定位与正文不一致")
        if not parsed.paragraphs and not parsed.tables:
            raise ValueError("文档没有可回链的段落或表格证据")
        if len(parsed.paragraphs) + sum(len(table) for table in parsed.tables) > 2000:
            raise ValueError("文档段落超过分析上限，请明确选择较小交付")
        documents.append({**source, "title": parsed.title, "paragraph_count": len(parsed.paragraphs),
                          "table_count": len(parsed.tables), "warnings": parsed.warnings,
                          "parsing_metadata": getattr(parsed, "metadata", {})})
        chunks = [(paragraph, {"paragraph": index, "page": None, **(locators[index - 1] if locators else {})})
                  for index, paragraph in enumerate(parsed.paragraphs, 1)]
        table_locators = getattr(parsed, "table_locators", [])
        if table_locators and len(table_locators) != len(parsed.tables):
            raise ValueError("文档表格定位与内容不一致")
        for table_no, table in enumerate(parsed.tables, 1):
            for row_no, row in enumerate(table, 1):
                chunks.append((" | ".join(row), {"table": table_no, "row": row_no, "page": None,
                              **(table_locators[table_no - 1] if table_locators else {})}))
        for index, (paragraph, locator) in enumerate(chunks, 1):
            if document_evidence >= 400 // len(sources) or document_characters + len(paragraph[:2000]) > 100000 // len(sources):
                continue
            characters += len(paragraph[:2000])
            document_characters += len(paragraph[:2000])
            document_evidence += 1
            evidence.append({"id": hashlib.sha256(f"{source['id']}:{source['sha256']}:{index}".encode()).hexdigest(),
                             "source_id": source["id"], "locator": locator,
                             "quote": paragraph[:2000]})
    return {"format": 1, "fact_stage_version": 4, "documents": documents, "evidence": evidence,
            "statistics": {"documents": len(documents), "paragraphs": sum(item["paragraph_count"] for item in documents),
                           "evidence_paragraphs": len(evidence), "evidence_characters": characters},
            "coverage": "bounded_excerpts; omitted paragraphs/pages are not analyzed; page unknown unless parser supplies provenance",
            "interpretations": [], "suggestions": [], "requires_review": True}


def _validate_claims(value: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, Any]:
    validated = validate_ai_output(value, {"interpretations": list, "suggestions": list})
    lookup = {item["id"]: item for item in evidence}
    for kind in ("interpretations", "suggestions"):
        claims = validated.get(kind, [])
        if len(claims) > 20:
            raise ValueError("模型解释超出报告上限")
        for claim in claims:
            if not isinstance(claim, dict) or set(claim) != {"text", "uncertainty", "citations"}:
                raise ValueError("模型解释需要 text/uncertainty/citations")
            if not isinstance(claim["text"], str) or not claim["text"] or not isinstance(claim["uncertainty"], str) or not claim["uncertainty"]:
                raise ValueError("模型解释缺少内容或不确定性")
            citations = claim["citations"]
            if not isinstance(citations, list) or not citations:
                raise ValueError("模型解释缺少证据引用")
            for citation in citations:
                if not isinstance(citation, dict) or set(citation) != {"evidence_id", "quote"}:
                    raise ValueError("模型引用格式无效")
                reference = lookup.get(citation["evidence_id"]) if isinstance(citation["evidence_id"], str) else None
                quote = citation["quote"]
                if reference is None or not isinstance(quote, str) or not quote.strip() or quote not in reference["quote"]:
                    raise ValueError("模型引用无法回到选定原文")
    return validated


def execute(manifest: Path, output: Path, *, config_path: Path | None = None, use_ai: bool = False,
            selected_ids: list[str] | None = None) -> dict[str, Any]:
    sources = _verified_sources(manifest, selected_ids)
    identity = hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()
    if output.is_symlink():
        raise ValueError("报告目录不能使用链接")
    output.mkdir(parents=True, exist_ok=True)
    facts_path = output / "facts.json"
    report_path = output / "analysis.json"
    report = None
    if facts_path.is_file():
        try:
            cached = _json(facts_path)
            digest = cached.pop("stage_sha256", None)
            if cached.get("fact_stage_version") == 4 and cached.get("input_sha256") == identity and digest == _stage_digest(cached):
                report = cached
        except (ValueError, OSError):
            pass
    if report is None:
        report = _facts(sources)
    report["input_sha256"] = identity
    report["stage_sha256"] = _stage_digest(report)
    # Persist the deterministic stage before any optional external operation.
    atomic_write(facts_path, json.dumps(report, ensure_ascii=False, indent=2).encode())
    report = {**report, "status": "completed_local", "model": None}
    if use_ai:
        if config_path is None:
            raise ValueError("模型分析需要明确的任务配置与隐私/预算")
        try:
            config = load_config(config_path)
            ai = config.section("ai")
            provider = build_provider(ai, app_config=config, egress=EgressBroker(config))
            provider.check_content_allowed("allow_page_text", "明确选择的交付文档")
            limits = ai.get("budget", {})
            provider.max_tokens = min(int(getattr(provider, "max_tokens", 0) or 4096), 4096)
            provider.budget = AIBudget(maximum_requests=1, maximum_tokens=int(limits.get("maximum_tokens", 0)),
                                       maximum_cost=float(limits.get("max_cost", 0)))
            # Send only the selected bounded excerpts, never other archive files.
            selected: list[dict[str, Any]] = []
            groups = [[item for item in report["evidence"] if item["source_id"] == source["id"]] for source in sources]
            ordered = [item for row in zip_longest(*groups) for item in row if item is not None]
            for item in ordered:
                proposed = [*selected, item]
                if len(json.dumps(proposed, ensure_ascii=False)) > min(12000, int(limits.get("maximum_input_characters", 0)) or 12000):
                    break
                selected = proposed
            if not selected:
                raise ValueError("输入字符预算不足，事实阶段已保留")
            selected_ids = sorted({item["source_id"] for item in selected})
            report["model_scope"] = {"source_ids": selected_ids,
                "omitted_source_ids": [source["id"] for source in sources if source["id"] not in selected_ids],
                "evidence_ids": [item["id"] for item in selected]}
            prompt = "对已选资料做比较。仅输出 JSON interpretations/suggestions 列表；每项只含 text/uncertainty/citations。citations 每项 evidence_id/quote，quote 必须逐字来自证据。不要声称因果已证明。\n" + mark_untrusted(json.dumps(selected, ensure_ascii=False))
            maximum = int(limits.get("maximum_input_characters", 0))
            if maximum and len(prompt) > maximum:
                raise ValueError("完整提示超过输入字符预算，事实阶段已保留")
            response = provider.generate([{"role": "user", "content": prompt}])
            claims = _validate_claims(json.loads(response.text), selected)
            report.update(claims)
            report.update(status="completed_with_interpretations", model={"provider": response.provider, "model": response.model},
                          budget={"requests": provider.budget.requests, "tokens": provider.budget.tokens, "estimated_cost": provider.budget.cost})
        except Exception as exc:  # noqa: BLE001 - preserve verified facts and resumable stage on provider/schema/budget failure
            report.update(status="paused", failure=type(exc).__name__, next_action="retry_selected_analysis")
    atomic_write(report_path, json.dumps(report, ensure_ascii=False, indent=2).encode())
    return {"status": report["status"], "report": str(report_path), "facts": str(facts_path),
            "input_sha256": identity, "requires_review": True}


def _stage_digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
