"""EU AI Act Art. 53(1)(d) 训练内容公开摘要（general-purpose AI 模型）。

法条依据（**已核实到欧盟官方来源**，非二手转述）
--------------------------------------------------
* **Regulation (EU) 2024/1689 Art. 53(1)(d)**：提供者须「draw up and make publicly
  available a sufficiently detailed summary about the content used for training of
  the general-purpose AI model, **according to a template provided by the AI
  Office**」。
* 委员会于 **2025-07-24** 发布《Explanatory Notice and Template》
  （C(2025) 5235 final），并**明确该模板是唯一指引**（"sole guidance"）、
  **必须使用**。模板三节结构：
  1. **General information** —— 提供者/模型标识、模态、各模态规模（区间）、
     训练数据的一般特征；
  2. **List of data sources** —— 公开数据集、商业授权、第三方私有、
     **从网上抓取/爬取的内容**（含爬虫标识、采集时段、**最相关域名摘要**）、
     用户数据、合成数据；
  3. **Relevant data processing aspects** —— Directive (EU) 2019/790 Art. 4(3)
     项下 TDM 权利保留的识别与遵守、违法内容移除。
* 时间线：义务自 **2025-08-02** 适用；**2025-08-02 前**投放的模型须在
  **2027-08-02** 前发布摘要；AI Office 的监督执法自 **2026-08-02** 起；
  罚则上限为全球年营业额 3% 或 €15,000,000（取高者）。
  摘要须**每 6 个月或实质性重训练后更新**。开源许可模型同样适用。
* 提供者即便尽力仍无法提供某些信息，**必须在摘要中明确说明并说明理由**。

三条设计约束（都是"错了会被误读"而不是"少了不好看"）
------------------------------------------------------
1. **取不到就写 ``GAP`` 并附理由，绝不写空白。** 空白会被读成"无需申报"，
   而法条要求的是"明确说明并说明理由"——空白恰好把义务变成了沉默。
2. **四类哈希分离**，第三方可各自独立重算：
   ``artifact_set_sha256``（全部产物）/ ``filter_config_sha256``（本次运行的有效
   配置）/ ``verified_set_sha256``（**仅通过完整性校验**的产物）/ ``summary_sha256``
   （摘要正文）。把配置混进内容哈希，就没人能独立验证"这份摘要对应哪些数据"了。
3. **域名摘要按"最相关"而非全量罗列。** 模板要的是「a summary of the most
   relevant domain names」，不是域名全表；本模块按产物字节数降序取前 N，
   并同时给出**域名总数**，避免"截断"被误读成"只有这些"。
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..core.utils import utcnow
from ..state import StateStore
from .artifact_integrity import verify_artifacts

#: 模板要求的"最相关域名"条数上限（模板原文是 summary，不是全量清单）
TOP_DOMAINS = 20

#: 规模披露用"区间"而非精确值（模板要求 broad ranges）
_SIZE_BUCKETS: tuple[tuple[str, int], ...] = (
    ("<1 MB", 1 << 20),
    ("1-10 MB", 10 << 20),
    ("10-100 MB", 100 << 20),
    ("100 MB-1 GB", 1 << 30),
    (">=1 GB", 0),
)


class _Gap:
    """取不到的信息：一律用 GAP + 理由，**不写空白**。"""

    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def to_dict(self) -> dict[str, str]:
        return {"status": "GAP", "reason": self.reason}


def _gap(reason: str) -> dict[str, str]:
    return _Gap(reason).to_dict()


def _sha256_rows(rows: list[tuple[str, str]]) -> str:
    """对一组 (键, 值) 排序后取 sha256——**只覆盖内容本身**，不含配置。

    刻意不掺配置：这样第三方拿同一批产物就能独立重算，不必知道我们的运行参数。
    """
    digest = hashlib.sha256()
    for key, value in sorted(rows):
        digest.update(f"{key}\x00{value}\n".encode())
    return digest.hexdigest()


def _size_bucket(total: int) -> str:
    for label, ceiling in _SIZE_BUCKETS:
        if ceiling == 0 or total < ceiling:
            return label
    return ">=1 GB"


def _modality(content_type: str | None) -> str:
    """把 content-type 归到模板说的"模态"。"""
    value = str(content_type or "").lower()
    if "html" in value:
        return "text/html"
    if "json" in value:
        return "text/json"
    if "csv" in value:
        return "text/csv"
    if value.startswith("text/"):
        return "text/plain"
    if "pdf" in value:
        return "application/pdf"
    return "UNKNOWN" if not value else "other"


def _domain(url: str) -> str:
    host = (urlsplit(url).hostname or "").casefold()
    return host or "UNKNOWN"


def build_training_content_summary(
    state: StateStore,
    run_id: str | None = None,
    *,
    workspace: Path | None = None,
    effective_config: dict[str, Any] | None = None,
    provider_name: str = "",
    model_name: str = "",
) -> dict[str, Any]:
    """按委员会模板三节结构产出训练内容摘要。

    Args:
        state: 状态库（读 ``runs`` / ``artifacts`` / ``records``）。
        run_id: 限定某次运行；None 表示全部运行。
        workspace: 校验 ``artifacts.local_path`` 的根目录。
        effective_config: 本次运行的**有效配置**；仅用于 ``filter_config_sha256``
            与 TDM 声明，**不进内容哈希**。
        provider_name / model_name: 模板第 1 节要求的标识信息；留空则记 GAP。
    """
    where, params = (" WHERE run_id=?", (run_id,)) if run_id else ("", ())
    run_rows = state.rows(f"SELECT * FROM runs{where} ORDER BY started_at", params)
    artifacts = state.rows(
        "SELECT source_url, sha256, content_type, size_bytes, created_at FROM artifacts"
        + where.replace("run_id=?", "run_id=?")
        + " ORDER BY source_url",
        params,
    )
    records = state.rows(
        "SELECT record_id, source_url, created_at FROM records" + where + " ORDER BY record_id",
        params,
    )

    integrity = verify_artifacts(state, run_id, workspace=workspace)
    verified = {item["source_url"] for item in integrity["items"] if item["status"] == "valid"}

    # ── 四类哈希（分离，第三方可各自独立重算）──────────────────────────────
    artifact_set = _sha256_rows([(str(a["source_url"]), str(a["sha256"])) for a in artifacts])
    verified_set = _sha256_rows(
        [(str(a["source_url"]), str(a["sha256"])) for a in artifacts if str(a["source_url"]) in verified]
    )
    filter_config = hashlib.sha256(
        json.dumps(effective_config or {}, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()

    # ── 第 1 节：General information ──────────────────────────────────────
    by_modality: Counter[str] = Counter()
    bytes_by_modality: Counter[str] = Counter()
    for row in artifacts:
        modality = _modality(row["content_type"])
        by_modality[modality] += 1
        bytes_by_modality[modality] += int(row["size_bytes"] or 0)

    run_span = (
        {"started_at": run_rows[0]["started_at"], "finished_at": run_rows[-1].get("finished_at")}
        if run_rows
        else _gap("状态库里没有 runs 记录，无法给出采集时段")
    )
    section_general: dict[str, Any] = {
        "provider": provider_name or _gap("未提供提供者名称（模板第 1 节必填，由发布方填入）"),
        "model": model_name or _gap("未提供模型名称（模板第 1 节必填，由发布方填入）"),
        "run_ids": [str(row["run_id"]) for row in run_rows] or None
        or _gap("状态库里没有 runs 记录"),
        "collection_period": run_span,
        "modality_sizes": [
            {
                "modality": modality,
                "documents": by_modality[modality],
                "size_bucket": _size_bucket(bytes_by_modality[modality]),
                "size_bytes": bytes_by_modality[modality],
            }
            for modality in sorted(by_modality)
        ]
        or _gap("本次运行没有产物记录，无法给出模态规模"),
        "records_extracted": len(records),
    }

    # ── 第 2 节：List of data sources ─────────────────────────────────────
    per_domain_bytes: Counter[str] = Counter()
    per_domain_docs: Counter[str] = Counter()
    for row in artifacts:
        host = _domain(str(row["source_url"]))
        per_domain_bytes[host] += int(row["size_bytes"] or 0)
        per_domain_docs[host] += 1
    top = per_domain_bytes.most_common(TOP_DOMAINS)

    section_sources: dict[str, Any] = {
        "scraped_online_content": {
            "domain_count": len(per_domain_bytes),
            "top_domains": [
                {
                    "domain": host,
                    "documents": per_domain_docs[host],
                    "size_bytes": size,
                    "size_bucket": _size_bucket(size),
                }
                for host, size in top
            ],
            "truncated": len(per_domain_bytes) > TOP_DOMAINS,
            "user_agent": _user_agent(effective_config),
        },
        "publicly_available_datasets": _gap(
            "本摘要只覆盖经 OmniCrawler 采集的产物；未使用任何现成公开数据集"
        ),
        "commercially_licensed_datasets": _gap("同上：未使用商业授权数据集"),
        "private_third_party_datasets": _gap("同上：未使用第三方私有数据集"),
        "user_data": _gap("同上：不含任何平台用户数据"),
        "synthetic_data": _gap("同上：不含蒸馏或对齐用合成数据"),
        "template_declared_sources": _template_sources(),
    }

    # ── 第 3 节：Relevant data processing aspects ────────────────────────
    coverage = (len(verified) / len(artifacts)) if artifacts else 0.0
    section_processing: dict[str, Any] = {
        "reservation_of_rights_tdm": {
            "robots_txt_respected": _robots_flag(effective_config),
            "note": "TDM 权利保留（Directive (EU) 2019/790 Art. 4(3)）的识别与遵守，"
            "由 Art. 53(1)(c) 的版权政策承载；本摘要只声明采集侧的实际行为。",
        },
        "integrity_verification": {
            "sha256_coverage": round(coverage, 4),
            "verified": integrity["valid"],
            "missing": integrity["missing"],
            "corrupt": integrity["corrupt"],
            "total": integrity["total"],
        },
        "illegal_content_removal": _gap(
            "本工具不做内容违法性判定，也不提供下架/移除流水线；"
            "该环节需由发布方在模型侧说明"
        ),
    }

    body: dict[str, Any] = {
        "template": {
            "name": "Public Summary of Training Content for General-Purpose AI models",
            "legal_basis": "Regulation (EU) 2024/1689 Art. 53(1)(d)",
            "published_by": "European Commission / AI Office",
            "published_on": "2025-07-24",
            "reference": "C(2025) 5235 final",
            "mandatory": "使用该模板是强制的（官方称其为唯一指引）",
        },
        "section_1_general_information": section_general,
        "section_2_list_of_data_sources": section_sources,
        "section_3_data_processing_aspects": section_processing,
        "hashes": {
            "note": "四类哈希**分离**：第三方可各自独立重算，无需知道运行配置。",
            "artifact_set_sha256": artifact_set,
            "verified_set_sha256": verified_set,
            "filter_config_sha256": filter_config,
        },
        "generated_at": utcnow(),
        "generated_by": "omnicrawler.quality.training_content_summary",
    }
    body["hashes"]["summary_sha256"] = _sha256_rows(_stable(body))
    return body


def _stable(value: Any) -> list[tuple[str, str]]:
    """把摘要正文摊平成 (路径, 值) 列表，便于逐项哈希（跳过 generated_at 与自身）。"""
    flat: list[tuple[str, str]] = []

    def walk(prefix: str, node: Any) -> None:
        if isinstance(node, dict):
            for key in sorted(node):
                if prefix == "" and key in ("generated_at", "hashes"):
                    continue
                walk(f"{prefix}.{key}" if prefix else key, node[key])
        elif isinstance(node, (list, tuple)):
            for index, item in enumerate(node):
                walk(f"{prefix}[{index}]", item)
        else:
            flat.append((prefix, str(node)))

    walk("", value)
    return flat


def _robots_flag(effective_config: dict[str, Any] | None) -> dict[str, Any]:
    if not effective_config:
        return _gap("未提供有效配置，无法声明 robots 遵从行为")
    http = effective_config.get("http")
    respected = bool(http.get("respect_robots")) if isinstance(http, dict) else False
    return {
        "respect_robots": respected,
        "user_agent": str(http.get("user_agent", "")) if isinstance(http, dict) else "",
    }


def _user_agent(effective_config: dict[str, Any] | None) -> Any:
    if not effective_config or not isinstance(effective_config.get("http"), dict):
        return _gap("未提供有效配置，无法声明采集标识")
    return str(effective_config["http"].get("user_agent", ""))


def _template_sources() -> Any:
    """模板侧声明的来源（``source_urls`` / ``license`` / ``verified_at``）。

    这是**模板元数据**层面的来源声明，与产物层面的域名摘要是两回事：前者是人工
    声明的来源与许可，后者是实测的抓取事实。两者都列，读者才能互相印证。
    """
    try:
        from ..templates.template_catalog import bundled_template_catalog

        records = bundled_template_catalog().discover()
    except Exception as exc:  # noqa: BLE001 — 目录不可用时如实记 GAP，不静默
        return _gap(f"模板目录不可用，无法给出模板声明的来源：{type(exc).__name__}: {exc}")

    declared = [
        {
            "template_id": record.metadata.template_id,
            "source_urls": list(record.metadata.source_urls),
            "license": record.metadata.license,
            "verified_at": record.metadata.verified_at or None
            or _gap("模板元数据未填 verified_at"),
        }
        for record in records
        if record.metadata.source_urls
    ]
    return declared or _gap("没有模板声明 source_urls（模板元数据里为空）")


def write_training_content_summary(summary: dict[str, Any], path: Path) -> Path:
    """落盘摘要（UTF-8、缩进 2、不转义非 ASCII）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
