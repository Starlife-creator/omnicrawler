"""EU AI Act Art. 53(1)(d) 训练内容摘要的守卫。

这个摘要的判据不是"字段全了没"，而是**三件会被误读的事**：

1. **空白 vs GAP。** 法条要求"明确说明并说明理由"；空白会被读成"无需申报"。
   ⇒ 取不到的字段必须是 ``{"status": "GAP", "reason": ...}``，且**必须带理由**。
2. **哈希分离。** 四类哈希混在一起，就没人能独立验证"这份摘要对应哪些数据"。
   ⇒ 每类哈希都必须在**只喂它该覆盖的东西**时保持稳定，且不掺配置。
3. **截断必须可见。** 模板要的是"最相关域名摘要"，不是域名全表；截断了却不说，
   读者会以为"只有这些域名"。

本文件全部离线：状态库用临时目录自建，不联网、不跑管线。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omnicrawler.quality.training_content_summary import (
    TOP_DOMAINS,
    build_training_content_summary,
    write_training_content_summary,
)
from omnicrawler.state import StateStore


def _seed(
    tmp_path: Path,
    specs: list[tuple[str, str, int]],
    *,
    run_id: str | None = None,
    content_type: str = "text/html; charset=utf-8",
) -> tuple[StateStore, Path, str]:
    """用**公开 API** 造一个带 runs/artifacts/records 的状态库。

    ``local_path`` 必须真实存在且内容对得上，否则 ``verify_artifacts`` 会判
    missing/corrupt —— 而那正是摘要第 3 节要如实报告的东西，不能靠 mock 绕过。
    """

    from omnicrawler.core.models import CrawlRequest, ExtractedRecord, FetchResult

    workspace = tmp_path / "ws"
    (workspace / "raw").mkdir(parents=True, exist_ok=True)
    state = StateStore(workspace / "state.sqlite3")
    if run_id is None:
        run_id = state.start_run("demo", str(workspace / "task.yaml"))

    for index, (url, body, _size) in enumerate(specs):
        path = workspace / "raw" / f"a{index}.html"
        payload = body.encode("utf-8")
        path.write_bytes(payload)
        request = CrawlRequest(url)
        result = FetchResult(
            request, url, 200, {"content-type": content_type}, payload, 0.1
        )
        state.save_artifact(run_id, result, path)
        state.save_records(
            run_id, request, [ExtractedRecord(url, "item", {"title": f"t{index}"})]
        )
    state.finish_run(run_id, "succeeded", {"records": len(specs)})
    return state, workspace, run_id


def _summary(
    tmp_path: Path, specs: list[tuple[str, str, int]], *, run_id: str | None = "auto", **kw
) -> dict:
    state, workspace, actual = _seed(tmp_path, specs)
    scope = actual if run_id == "auto" else run_id
    return build_training_content_summary(state, scope, workspace=workspace, **kw)


def _resummarize(state: StateStore, workspace: Path, run_id: str, **kw) -> dict:
    """在**同一个状态库**上重算摘要。

    刻意不重新 seed：`collection_period` 取自 `runs.started_at/finished_at`，
    而采集时段本来就是摘要所断言的内容，必须进哈希。换个状态库重算，时间戳变了、
    哈希理应跟着变——那不是不确定性，是断言内容变了。
    """
    return build_training_content_summary(state, run_id, workspace=workspace, **kw)


# ── 模板结构 ────────────────────────────────────────────────────────────────


def test_summary_follows_the_three_section_template(tmp_path: Path) -> None:
    body = _summary(tmp_path, [("https://a.example/p1", "<html>x</html>", 12)])

    assert body["template"]["legal_basis"] == "Regulation (EU) 2024/1689 Art. 53(1)(d)"
    # 委员会模板的三节必须都在，且用官方节名
    assert set(body) >= {
        "section_1_general_information",
        "section_2_list_of_data_sources",
        "section_3_data_processing_aspects",
    }


def test_no_data_yields_gaps_not_blanks(tmp_path: Path) -> None:
    """★ 空状态库：每个取不到的字段都必须是带理由的 GAP，不能是空白/None。

    这是本模块最要紧的判据——空白会被读成"无需申报"，而法条要求"明确说明理由"。
    """
    workspace = tmp_path / "empty"
    workspace.mkdir()
    body = build_training_content_summary(
        StateStore(workspace / "s.sqlite3"), None, workspace=workspace
    )

    general = body["section_1_general_information"]
    assert general["provider"]["status"] == "GAP"
    assert general["provider"]["reason"].strip(), "GAP 必须带理由"
    assert general["run_ids"]["status"] == "GAP"
    assert general["modality_sizes"]["status"] == "GAP"

    sources = body["section_2_list_of_data_sources"]
    for key in (
        "publicly_available_datasets",
        "commercially_licensed_datasets",
        "private_third_party_datasets",
        "user_data",
        "synthetic_data",
    ):
        assert sources[key]["status"] == "GAP", f"{key} 必须是 GAP，不能留空"
        assert sources[key]["reason"].strip(), f"{key} 的 GAP 必须带理由"


def test_gap_is_never_a_bare_empty_value(tmp_path: Path) -> None:
    """全量扫描：任何取不到的字段都不得是 ``""`` / ``[]`` / ``None``。"""
    workspace = tmp_path / "empty"
    workspace.mkdir()
    body = build_training_content_summary(
        StateStore(workspace / "s.sqlite3"), None, workspace=workspace
    )
    offenders: list[str] = []

    def walk(prefix: str, node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(f"{prefix}.{key}", value)
        elif isinstance(node, list):
            for index, item in enumerate(node):
                walk(f"{prefix}[{index}]", item)
        elif isinstance(node, str) and not node.strip():
            offenders.append(prefix)
        elif node is None:
            offenders.append(prefix)

    walk("", body)
    assert offenders == [], f"这些字段是空白/None，会被读成『无需申报』：{offenders}"


# ── 哈希分离 ────────────────────────────────────────────────────────────────


def test_hashes_are_separated_and_recomputable(tmp_path: Path) -> None:
    """★ 四类哈希分离：改配置不许动内容哈希，改内容不许动配置哈希。"""
    specs = [("https://a.example/p1", "<html>x</html>", 12)]
    state, workspace, run_id = _seed(tmp_path, specs)
    cfg1 = {"http": {"user_agent": "ua-1"}}
    cfg2 = {"http": {"user_agent": "ua-2"}}

    base = _resummarize(state, workspace, run_id, effective_config=cfg1)
    same = _resummarize(state, workspace, run_id, effective_config=cfg1)
    other_config = _resummarize(state, workspace, run_id, effective_config=cfg2)

    more_state, more_ws, more_run = _seed(
        tmp_path, [*specs, ("https://b.example/p2", "<html>y</html>", 12)]
    )
    more_data = _resummarize(more_state, more_ws, more_run, effective_config=cfg1)

    hashes = base["hashes"]
    assert hashes["artifact_set_sha256"] == same["hashes"]["artifact_set_sha256"]
    # 改配置 ⇒ 只有 filter_config 变；内容哈希必须**纹丝不动**
    assert other_config["hashes"]["filter_config_sha256"] != hashes["filter_config_sha256"]
    assert other_config["hashes"]["artifact_set_sha256"] == hashes["artifact_set_sha256"]
    # 加数据 ⇒ 内容哈希变，配置哈希不变
    assert more_data["hashes"]["artifact_set_sha256"] != hashes["artifact_set_sha256"]
    assert more_data["hashes"]["filter_config_sha256"] == hashes["filter_config_sha256"]
    assert hashes["summary_sha256"] == same["hashes"]["summary_sha256"]
    assert more_data["hashes"]["summary_sha256"] != hashes["summary_sha256"]


def test_artifact_hash_ignores_config_entirely(tmp_path: Path) -> None:
    """反向断言：内容哈希不得掺入配置——否则第三方无法独立重算。"""
    specs = [("https://a.example/p1", "<html>x</html>", 12)]
    state, workspace, run_id = _seed(tmp_path, specs)
    with_cfg = _resummarize(state, workspace, run_id, effective_config={"http": {"proxy": "http://p:1"}})
    without = _resummarize(state, workspace, run_id)
    assert with_cfg["hashes"]["artifact_set_sha256"] == without["hashes"]["artifact_set_sha256"]


def test_summary_hash_is_stable_across_regeneration(tmp_path: Path) -> None:
    """`generated_at` 每次都变，所以它**不得**进摘要哈希，否则哈希无意义。"""
    specs = [("https://a.example/p1", "<html>x</html>", 12)]
    state, workspace, run_id = _seed(tmp_path, specs)
    first = _resummarize(state, workspace, run_id)
    second = _resummarize(state, workspace, run_id)
    assert first["hashes"]["summary_sha256"] == second["hashes"]["summary_sha256"]


# ── 第 2 节：域名摘要与截断可见 ────────────────────────────────────────────


def test_top_domains_are_ranked_and_truncation_is_visible(tmp_path: Path) -> None:
    # 每个域名一条记录、体积逐个递增 ⇒ 排序与截断都可预期
    specs = [
        (f"https://d{i}.example/p", f"<html>{'x' * (i + 1)}</html>", i + 1)
        for i in range(TOP_DOMAINS + 3)
    ]
    body = _summary(tmp_path, specs)

    scraped = body["section_2_list_of_data_sources"]["scraped_online_content"]
    assert scraped["domain_count"] == TOP_DOMAINS + 3
    assert len(scraped["top_domains"]) == TOP_DOMAINS
    assert scraped["truncated"] is True, "截断了必须显式说明，否则读者会以为只有这些域名"
    sizes = [item["size_bytes"] for item in scraped["top_domains"]]
    assert sizes == sorted(sizes, reverse=True), "域名应按字节数降序（模板要『最相关』）"


def test_no_truncation_flag_when_all_domains_fit(tmp_path: Path) -> None:
    body = _summary(tmp_path, [("https://a.example/p", "<html>x</html>", 12)])
    scraped = body["section_2_list_of_data_sources"]["scraped_online_content"]
    assert scraped["truncated"] is False
    assert scraped["domain_count"] == len(scraped["top_domains"])


# ── 第 3 节：完整性覆盖如实报告 ────────────────────────────────────────────


def test_integrity_section_reports_actual_coverage(tmp_path: Path) -> None:
    body = _summary(tmp_path, [("https://a.example/p1", "<html>x</html>", 12)])
    integrity = body["section_3_data_processing_aspects"]["integrity_verification"]
    assert integrity["total"] == 1
    assert integrity["verified"] == 1
    assert integrity["missing"] == 0
    assert integrity["corrupt"] == 0
    assert integrity["sha256_coverage"] == 1.0


def test_corrupt_artifact_is_reported_not_hidden(tmp_path: Path) -> None:
    """产物被改动 ⇒ 摘要必须报 corrupt，而不是照样声称全覆盖。"""
    state, workspace, run_id = _seed(tmp_path, [("https://a.example/p1", "<html>x</html>", 12)])
    victim = next((workspace / "raw").iterdir())
    victim.write_text("tampered", encoding="utf-8")

    body = build_training_content_summary(state, run_id, workspace=workspace)
    integrity = body["section_3_data_processing_aspects"]["integrity_verification"]
    assert integrity["corrupt"] == 1
    assert integrity["verified"] == 0
    assert integrity["sha256_coverage"] == 0.0
    # 内容哈希与校验集哈希必须**分开**：被篡改的产物仍在 artifact_set 里
    assert body["hashes"]["artifact_set_sha256"] != body["hashes"]["verified_set_sha256"]


# ── 落盘 ────────────────────────────────────────────────────────────────────


def test_written_summary_is_valid_utf8_json(tmp_path: Path) -> None:
    body = _summary(tmp_path, [("https://a.example/p1", "<html>中文</html>", 15)])
    target = write_training_content_summary(body, tmp_path / "out" / "summary.json")

    assert target.is_file()
    reloaded = json.loads(target.read_text(encoding="utf-8"))
    assert reloaded["hashes"]["summary_sha256"] == body["hashes"]["summary_sha256"]
    # 非 ASCII 不得被转义成 \uXXXX（模板要给第三方读）
    assert "\\u" not in target.read_text(encoding="utf-8")


def test_summary_is_deterministic_for_same_inputs(tmp_path: Path) -> None:
    """同输入 ⇒ 同摘要（除 generated_at 外）。这是"可验证"的前提。"""
    specs = [("https://a.example/p1", "<html>x</html>", 12),
             ("https://a.example/p2", "<html>y</html>", 12)]
    state, workspace, run_id = _seed(tmp_path, specs)
    first = _resummarize(state, workspace, run_id)
    second = _resummarize(state, workspace, run_id)
    first.pop("generated_at"), second.pop("generated_at")
    assert json.dumps(first, sort_keys=True, ensure_ascii=False) == json.dumps(
        second, sort_keys=True, ensure_ascii=False
    )


@pytest.mark.parametrize("run_id", ["run-1", None])
def test_run_scope_is_respected(tmp_path: Path, run_id: str | None) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    state = StateStore(workspace / "s.sqlite3")
    run_a = state.start_run("p", str(workspace / "a.yaml"))
    run_b = state.start_run("p", str(workspace / "b.yaml"))
    assert run_a != run_b

    scoped = build_training_content_summary(state, run_a, workspace=workspace)
    everything = build_training_content_summary(state, None, workspace=workspace)
    assert scoped["section_1_general_information"]["run_ids"] == [run_a]
    assert everything["section_1_general_information"]["run_ids"] == [run_a, run_b]


# ── 导出层接线 ──────────────────────────────────────────────────────────────


def _pipeline_config(tmp_path: Path, *, ai_act: bool) -> tuple:
    """最小可用配置：走真实 ``export_all``，验证 `outputs.ai_act_summary` 开关。"""
    import yaml

    from omnicrawler.core.config import DEFAULTS, AppConfig, deep_merge

    workspace = tmp_path / ("ws_on" if ai_act else "ws_off")
    (workspace / "raw").mkdir(parents=True, exist_ok=True)
    raw = deep_merge(dict(DEFAULTS), {"outputs": {"ai_act_summary": ai_act}})
    raw["project"] = {"name": "ai-act", "workspace": str(workspace)}
    path = tmp_path / "task.yaml"
    path.write_text(yaml.safe_dump({"project": {"name": "ai-act"}}, sort_keys=False), encoding="utf-8")
    return AppConfig(path, tmp_path, raw, workspace), workspace


def test_export_writes_summary_only_when_enabled(tmp_path: Path) -> None:
    """默认关闭 ⇒ 不产出；显式开启 ⇒ 产出且形状可读。"""
    from omnicrawler.pipeline.exporters import export_all

    off_config, off_ws = _pipeline_config(tmp_path, ai_act=False)
    with StateStore(off_ws / "state.sqlite3") as off_state:
        export_all(off_config, off_state, None)
    assert not (off_ws / "output" / "ai_act_training_summary.json").exists()

    on_config, on_ws = _pipeline_config(tmp_path, ai_act=True)
    with StateStore(on_ws / "state.sqlite3") as on_state:
        files = export_all(on_config, on_state, None)
    summary_path = on_ws / "output" / "ai_act_training_summary.json"
    assert summary_path.is_file()
    assert files["files"]["ai_act_training_summary"] == str(summary_path)

    body = json.loads(summary_path.read_text(encoding="utf-8"))
    assert body["template"]["legal_basis"].endswith("Art. 53(1)(d)")
    assert set(body) >= {
        "section_1_general_information",
        "section_2_list_of_data_sources",
        "section_3_data_processing_aspects",
    }
