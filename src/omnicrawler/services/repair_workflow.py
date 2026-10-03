"""Explicit local snapshot repair workflow using the existing quality state machine."""
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import yaml

from ..core.config import AppConfig, load_config
from ..core.models import CrawlRequest, FetchResult
from ..core.utils import atomic_write
from ..extraction.extractors import HTMLProcessor
from ..fetching.session_lease import session_lease
from ..quality.auto_apply import (
    AutoApplyPolicy,
    AutomationTier,
    classify_tier,
    observed_auto_apply,
    step_observe,
)
from ..quality.comparison_evidence import ComparisonSample, compare_snapshots
from ..quality.llm_candidate_generator import LLMCandidateGenerator
from ..quality.observation_store import ObservationStore
from ..quality.shadow_repair import RepairCandidate, candidate_rule, config_digest, shadow_config
from ..security.egress import EgressBroker
from .ai_providers import build_provider
from .ai_safety import AIBudget
from .config_history import ConfigHistory


def _read(path: Path) -> dict[str, Any]:
    if path.stat().st_size > 2 * 1024**2:
        raise ValueError("修复证据文件超过大小限制")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("修复证据必须是对象")
    return value


def _sample(value: dict[str, Any]) -> ComparisonSample:
    identity, url, html = value.get("sample_id"), value.get("url"), value.get("html")
    expected = value.get("expected", [])
    if not isinstance(identity, str) or not identity or not isinstance(url, str) or not url or not isinstance(html, str) or not html:
        raise ValueError("每个证据样本需要 sample_id/url/html")
    if not isinstance(expected, list) or not all(isinstance(item, dict) for item in expected):
        raise ValueError("样本 expected 必须是完整标注记录列表")
    response = FetchResult(CrawlRequest(url), url, 200, {"content-type": "text/html; charset=utf-8"}, html.encode(), 0)
    return ComparisonSample(identity, response, tuple(expected))


def _evidence(path: Path) -> tuple[ComparisonSample, list[ComparisonSample], list[ComparisonSample]]:
    bundle = _read(path)
    if bundle.get("format") != 1:
        raise ValueError("不支持的修复证据格式")
    training = _sample(bundle["training"])
    current = [_sample(value) for value in bundle.get("current", [])]
    historical = [_sample(value) for value in bundle.get("historical", [])]
    samples = [training, *current, *historical]
    identities = {item.sample_id for item in samples}
    hashes = {item.response.content_hash for item in samples}
    if not current or not historical or len(identities) != len(samples) or len(hashes) != len(samples):
        raise ValueError("训练、留出与历史样本须独立且非空，不能复用身份或正文")
    return training, current, historical


def _publish(config: AppConfig, candidate: RepairCandidate, audit: dict[str, Any]) -> str:
    # Keep unresolved source secrets and unknown fields. Never serialize the
    # expanded AppConfig.raw (which can contain resolved environment secrets).
    source = yaml.safe_load(config.path.read_text(encoding="utf-8"))
    modified = shadow_config(source, candidate)
    modified.pop("_shadow", None)
    modified["_repair"] = audit
    payload = yaml.safe_dump(modified, allow_unicode=True, sort_keys=False).encode()
    atomic_write(config.path, payload)
    return config_digest(load_config(config.path).raw)


def _observation_identity(current: list[ComparisonSample], historical: list[ComparisonSample]) -> str:
    # Renaming sample IDs or reordering a bundle is not fresh observation.
    values = [(index, sample.response.content_hash, sample.response.final_url, sample.expected)
              for index, group in enumerate((current, historical)) for sample in group]
    encoded = sorted(json.dumps(value, sort_keys=True, ensure_ascii=False) for value in values)
    return hashlib.sha256(json.dumps(encoded).encode()).hexdigest()


def _baseline(config: AppConfig, state: dict[str, Any]) -> AppConfig:
    history = Path(state["history"])
    root = (config.root / ".config_history").resolve()
    if root not in history.resolve().parents:
        raise ValueError("修复回滚快照不在配置历史内")
    payload = history.read_bytes()
    if hashlib.sha256(payload).hexdigest() != state["history_sha256"]:
        raise ValueError("修复回滚快照哈希不一致")
    # Use the actual loader beside the original file: preserve relative paths,
    # migrations and protected secret/env resolution without exporting secrets.
    with tempfile.NamedTemporaryFile(dir=config.path.parent, suffix=".yaml", delete=False) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    try:
        restored = load_config(temporary)
        return replace(config, raw=restored.raw)
    finally:
        temporary.unlink(missing_ok=True)


def execute(
    action: str, *, config_path: Path, evidence: Path | None = None,
    candidate_path: Path | None = None, generate: bool = False, max_rounds: int = 1,
) -> dict[str, Any]:
    if action not in {"preview", "apply", "observe", "rollback"} or not 1 <= max_rounds <= 3:
        raise ValueError("修复动作或轮次无效；轮次限 1–3")
    config = load_config(config_path)
    state_path = config.workspace / ".repair-active.json"
    report_path = config.workspace / "repair-preview.json"
    with session_lease(config.workspace):
        state = _read(state_path) if state_path.is_file() else {}
        if state and state.get("config_path") != str(config.path.resolve()):
            raise ValueError("工作区修复记录属于其他配置，请先完成其观察或回滚")
        with ObservationStore(config.workspace / "observation.sqlite3") as store:
            # Interrupted applications are recoverable, never silently adopted.
            if state.get("phase") == "prepared" and action != "rollback":
                raise ValueError("前次应用未完整提交，请先执行 repair rollback")
            if action == "rollback":
                if not state or state.get("phase") in {"rolled_back", "not_applied"}:
                    return {"status": "no_active_repair"}
                _baseline(config, state)
                if state.get("phase") == "applied" and config_digest(config.raw) != state["active_sha256"]:
                    raise ValueError("活跃配置已被编辑，不能自动覆盖；请审阅配置历史")
                if state.get("phase") == "prepared" and hashlib.sha256(config.path.read_bytes()).hexdigest() != state["history_sha256"]:
                    content = copy.deepcopy(config.raw)
                    content.pop("_repair", None)
                    if config_digest(content) != state["prepared_sha256"]:
                        raise ValueError("未完成应用后的配置已被编辑，不能自动覆盖")
                atomic_write(config.path, Path(state["history"]).read_bytes())
                store.mark_rolled_back(state["candidate"]["candidate_id"])
                state["phase"] = "rolled_back"
                atomic_write(state_path, json.dumps(state, ensure_ascii=False).encode())
                return {"status": "rolled_back", "candidate_id": state["candidate"]["candidate_id"]}
            if evidence is None:
                raise ValueError("需要明确选择的本地修复证据")
            training, current, historical = _evidence(evidence)
            active = config
            if action == "observe":
                if state.get("phase") != "applied" or config_digest(config.raw) != state.get("active_sha256"):
                    raise ValueError("没有匹配当前配置的已应用修复")
                active = _baseline(config, state)
                candidate = RepairCandidate(**{**state["candidate"], "supporting_samples": tuple(state["candidate"]["supporting_samples"]), "counterexamples": tuple(state["candidate"]["counterexamples"])})
                comparison = compare_snapshots(active, candidate, current, historical)
                if not comparison.evidence_sha256:
                    return {"status": "evidence_rejected", "comparison": asdict(comparison)}
                directive = step_observe(candidate, replace(comparison, evidence_sha256=_observation_identity(current, historical)), store)
                if directive is not None:
                    atomic_write(config.path, Path(state["history"]).read_bytes())
                    store.mark_rolled_back(candidate.candidate_id)
                    state["phase"] = "rolled_back"
                    atomic_write(state_path, json.dumps(state, ensure_ascii=False).encode())
                status = "rolled_back" if directive else "observing"
                if directive is None:
                    promoted = store.promote_for_candidate(candidate)
                    policy = AutoApplyPolicy(llm_enabled=str(config.section("ai").get("mode", "disabled")).lower() != "disabled")
                    if classify_tier(promoted, comparison, policy) is AutomationTier.L3:
                        audit = {**config.raw.get("_repair", {}), "status": "stable", "approved_by": "auto:L3"}
                        state["phase"] = "prepared"
                        atomic_write(state_path, json.dumps(state, ensure_ascii=False).encode())
                        state["active_sha256"] = _publish(config, candidate, audit)
                        state["phase"] = "applied"
                        atomic_write(state_path, json.dumps(state, ensure_ascii=False).encode())
                        status = "stable"
                return {"status": status, "comparison": asdict(comparison), "observations": store.snapshot(candidate.candidate_id)}
            if state.get("phase") == "applied" and action == "apply":
                raise ValueError("已有修复待观察；不能叠加覆盖回滚点")
            candidates: list[RepairCandidate] = []
            if generate:
                ai = config.section("ai")
                if str(ai.get("mode", "disabled")).lower() == "disabled":
                    raise ValueError("AI 未启用；可以提供本地候选规则进行确定性比较")
                provider = build_provider(ai, app_config=config, egress=EgressBroker(config))
                provider.check_content_allowed("allow_page_text", "已选择的训练快照")
                provider.max_tokens = min(int(getattr(provider, "max_tokens", 0) or 4096), 4096)
                budget = ai.get("budget", {})
                provider.budget = AIBudget(maximum_requests=min(max_rounds, int(budget.get("maximum_requests", 0)) or max_rounds), maximum_cost=float(budget.get("max_cost", 0)))
                input_characters = 0
                def call(prompt: str) -> str:
                    nonlocal input_characters
                    maximum = int(budget.get("maximum_input_characters", 0))
                    if maximum and input_characters + len(prompt) > maximum:
                        raise ValueError("修复输入字符预算已用完")
                    input_characters += len(prompt)
                    return str(provider.generate([{"role": "user", "content": prompt}]).text)
                generator = LLMCandidateGenerator(llm_generate=call)
                records = HTMLProcessor(config).process(training.response).records
                generated = generator.generate_candidates(training.response.body.decode("utf-8"), records, config.section("extract").get("fields", {}))
                candidates = [item.candidate for item in generated]
            elif candidate_path is not None:
                proposal = _read(candidate_path)
                field = str(proposal["field"])
                kind = str(proposal.get("rule_type", "css"))
                if kind not in {"css", "xpath"}:
                    raise ValueError("本入口只比较 HTML CSS/XPath 候选")
                key = "selector" if kind == "css" else kind
                old = config.section("extract").get("fields", {}).get(field, {}).get(key)
                new = str(proposal["new_rule"])
                if not isinstance(old, str) or not new or len(new) > 4000:
                    raise ValueError("候选字段不存在或规则无效")
                candidates = [candidate_rule(field, kind, old, new, (training.sample_id,))]
            else:
                raise ValueError("需要 --candidate 或 --generate")
            evaluated = [(candidate, compare_snapshots(config, candidate, current, historical)) for candidate in candidates]
            # Confidence comes from complete labeled production matches on the
            # holdout set, not LLM self-assessment or repeated training values.
            evaluated = [(
                replace(candidate_rule(candidate.field, candidate.rule_type, candidate.old_rule, candidate.new_rule,
                                       tuple(f"{sample.sample_id}:{index}" for sample in current for index, _record in enumerate(sample.expected))), origin=candidate.origin)
                if candidate.origin == "llm" and comparison.improves_safely else candidate,
                comparison,
            ) for candidate, comparison in evaluated]
            report = {"status": "preview", "historical_reference_only": True,
                      "candidates": [{"candidate": asdict(candidate), "comparison": asdict(comparison)} for candidate, comparison in evaluated]}
            atomic_write(report_path, json.dumps(report, ensure_ascii=False, indent=2).encode())
            if action == "preview" or not evaluated:
                return {**report, "report": str(report_path)}
            if len(evaluated) != 1:
                raise ValueError("一次只应用一个已比较候选，请先选择候选")
            candidate, comparison = evaluated[0]
            if not comparison.evidence_sha256 or not comparison.improves_safely:
                return {**report, "status": "evidence_rejected"}
            history = ConfigHistory(config.root / ".config_history").snapshot(config.path, reason="before_evidence_repair")
            assert history is not None
            state = {"format": 1, "config_path": str(config.path.resolve()), "phase": "prepared",
                     "candidate": asdict(candidate), "history": str(history),
                     "history_sha256": hashlib.sha256(history.read_bytes()).hexdigest()}
            prepared = shadow_config(config.raw, candidate)
            prepared.pop("_shadow", None)
            prepared.pop("_repair", None)
            state["prepared_sha256"] = config_digest(prepared)
            policy = AutoApplyPolicy(llm_enabled=generate)
            atomic_write(state_path, json.dumps(state, ensure_ascii=False).encode())
            result = observed_auto_apply(config.raw, shadow_config(config.raw, candidate), candidate,
                                         replace(comparison, evidence_sha256=_observation_identity(current, historical)), policy, store)
            if result is None:
                state["phase"] = "not_applied"
                atomic_write(state_path, json.dumps(state, ensure_ascii=False).encode())
                return {**report, "status": "approval_required"}
            if hashlib.sha256(config.path.read_bytes()).hexdigest() != state["history_sha256"]:
                raise ValueError("比较后配置文件已变化，请重新比较；原修改保留")
            state["active_sha256"] = _publish(config, candidate, result.config["_repair"])
            state["phase"] = "applied"
            atomic_write(state_path, json.dumps(state, ensure_ascii=False).encode())
            return {"status": "applied", "tier": result.tier.value, "candidate_id": candidate.candidate_id, "evidence_sha256": comparison.evidence_sha256}
