"""配置字段映射契约测试（正向链路）。

本测试固化 CrawlConfig → to_yaml → YAML → load_config → AppConfig.raw
的字段对应关系，防止字段名漂移。

重要认知前提：
1. 仅覆盖 A 类（GUI 可编辑）字段的正向链路。
   B 类（passthrough 透传）字段见 test_gui_config_preservation.py。
2. 仅覆盖正向链路。反向链路（from_yaml）存在已知不对称（如 rss→feed），
   不在本测试范围。
3. AppConfig.raw 不是 YAML 文件直接内容，而是经 migrate_config +
   expand_env_checked + deep_merge(DEFAULTS, ...) + resolve_secret_refs
   四层加工后的合并字典。断言对着 raw 做，不对着 YAML 文件做。
4. ai_api_key_ref 的 secret:// 引用会被 resolve_secret_refs 反向解析，
   本测试设为空串，断言 "api_key" not in provider。

修改字段映射者，需同步更新本文件与 ADR-005。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from omnicrawl.gui.core.config_model import CrawlConfig

# 依赖 ruamel.yaml（GUI 可选依赖）；缺包时整体跳过，与同目录其他测试一致
pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("ruamel") is None,
    reason="契约测试依赖 GUI 配置序列化（ruamel.yaml）",
)


def _build_full_config() -> CrawlConfig:
    """构造一个所有 A 类字段均赋非默认值且满足写入条件的 CrawlConfig。

    设计要点：
    - source_kind="rss"：触发 rss→feed 转换，断言时用字面量 "feed"
    - pagination 设置合法值（validate_config 会校验 start/end/parameter）
    - download.enabled=True：触发 extensions/output_dir 写入
    - incremental=True：触发 incremental 整段写入
    - ai_mode="cloud" + ai_provider 非空：触发 providers 写入
    - ai_api_key_ref=""：避免 secret:// 反向解析崩溃，断言改为存在性
    - process_pdf=False：避免模板文件存在性检查（环境耦合）
    - pdf_ocr="tesseract"：即使 process_pdf=False 也写入 ocr_backend，
      覆盖 pdf_ocr→ocr_backend 转换
    """
    from omnicrawl.gui.core.config_model import DownloadConfig, FieldDef

    return CrawlConfig(
        project_name="contract_test",
        workspace="work/contract",
        task_intent="monitor",
        task_description="契约测试用例",
        source_kind="rss",  # → YAML 写 "feed"
        seed_urls=["https://example.com/"],
        max_pages=50,
        delay=2.5,
        concurrency=8,
        user_agent="test-agent",
        respect_robots=False,
        fields=[FieldDef(
            name="title",
            selector="h1",
            selector_type="css",
            attribute="href",
            regex=r"^.{1,100}$",
            required=True,
            fallback_xpath="//h2",
        )],
        download=DownloadConfig(
            enabled=True,
            extensions=[".pdf"],
            output_dir="dl",
        ),
        topic_include_any=["关键词A"],
        topic_include_all=["关键词B"],
        topic_exclude=["排除词"],
        keep_uncertain_topics=False,
        process_pdf=False,
        pdf_ocr="tesseract",
        incremental=True,
        since_date="2026-01-01",
        monitor_same_url=True,
        output_formats=["jsonl", "parquet"],
        ai_mode="cloud",
        ai_provider="openai",
        ai_base_url="https://api.example.com",
        ai_model="gpt-test",
        ai_api_key_ref="",  # 空：避免 secret:// 反向解析
        extraction_mode="hybrid",
        ai_extraction_prompt="提取标题",
        ai_chunk_strategy="heading",
        ai_max_tokens_per_chunk=8000,
        resource_profile="performance",
        pagination={  # 合法值，validate_config 会校验
            "type": "page",
            "parameter": "page",
            "start": 1,
            "end": 10,
        },
    )


@pytest.fixture
def round_trip_pair(tmp_path: Path):
    """CrawlConfig → YAML → AppConfig.raw 的正向往返 fixture。

    返回 (original_crawl_config, loaded_app_config)。
    """
    from omnicrawl.core.config import load_config
    from omnicrawl.gui.core.config_serializer import to_yaml

    config = _build_full_config()
    yaml_str = to_yaml(config)
    yaml_path = tmp_path / "contract.yaml"
    yaml_path.write_text(yaml_str, encoding="utf-8")
    app_config = load_config(yaml_path)
    return config, app_config


# ---- 项目元数据 ----
def test_project_fields_mapped(round_trip_pair):
    config, app = round_trip_pair
    assert app.raw["project"]["name"] == config.project_name
    assert app.raw["project"]["workspace"] == config.workspace
    assert app.raw["project"]["intent"] == config.task_intent
    # task_description 仅 strip 后非空写入；fixture 设了非空值
    assert app.raw["project"]["description"] == config.task_description


# ---- 数据源（含 rss→feed 转换）----
def test_source_fields_mapped(round_trip_pair):
    config, app = round_trip_pair
    # source_kind="rss" → to_yaml 写 "feed"；断言用字面量，不写 == config.source_kind
    assert app.raw["source"]["kind"] == "feed"
    assert app.raw["source"]["seeds"] == config.seed_urls
    # pagination 非空时写入；fixture 设了合法值
    assert app.raw["source"]["pagination"] == config.pagination


# ---- 爬取参数（含 delay→delay_seconds 命名差异）----
def test_crawl_and_http_fields_mapped(round_trip_pair):
    config, app = round_trip_pair
    assert app.raw["crawl"]["max_pages"] == config.max_pages
    assert app.raw["crawl"]["concurrency"] == config.concurrency
    # delay → http.delay_seconds（命名不同）
    assert app.raw["http"]["delay_seconds"] == config.delay
    assert app.raw["http"]["user_agent"] == config.user_agent
    assert app.raw["http"]["respect_robots"] == config.respect_robots


# ---- 字段定义（含 selector_type/attr/regex/required/fallback_xpath）----
def test_fields_mapped(round_trip_pair):
    config, app = round_trip_pair
    raw_fields = app.raw["extract"]["fields"]
    assert "title" in raw_fields
    field = raw_fields["title"]
    assert field["selector"] == config.fields[0].selector
    # selector_type="css" 时 to_yaml 不写 type 键（config_serializer.py:112）
    assert "type" not in field
    assert field["attr"] == config.fields[0].attribute
    assert field["regex"] == config.fields[0].regex
    assert field["required"] is True
    # fallback_xpath → selectors[].xpath
    selectors = field.get("selectors", [])
    assert any(s.get("xpath") == config.fields[0].fallback_xpath for s in selectors)


# ---- 下载（含条件写入）----
def test_download_fields_mapped(round_trip_pair):
    config, app = round_trip_pair
    assert app.raw["download"]["enabled"] == config.download.enabled
    # extensions/output_dir 仅 enabled=True 时写入；fixture 满足
    assert app.raw["download"]["extensions"] == config.download.extensions
    assert app.raw["download"]["output_dir"] == config.download.output_dir


# ---- 主题筛选 ----
def test_topic_fields_mapped(round_trip_pair):
    config, app = round_trip_pair
    topic = app.raw["selection"]["topic"]
    assert topic["include_any"] == config.topic_include_any
    assert topic["include_all"] == config.topic_include_all
    assert topic["exclude"] == config.topic_exclude
    assert topic["keep_uncertain"] == config.keep_uncertain_topics


# ---- PDF 处理（含 pdf_ocr→ocr_backend/skip_ocr 转换）----
def test_pdf_fields_mapped(round_trip_pair):
    config, app = round_trip_pair
    pdf = app.raw["processors"]["pdf"]
    assert pdf["enabled"] == config.process_pdf
    # pdf_ocr="tesseract" → ocr_backend="tesseract", skip_ocr=False
    assert pdf["ocr_backend"] == "tesseract"
    # skip_ocr 由 to_yaml 无条件写入（config_serializer.py:147）
    assert pdf["skip_ocr"] is False


# ---- 增量与更新（含 incremental→skip_unchanged 命名差异）----
def test_incremental_and_updates_mapped(round_trip_pair):
    config, app = round_trip_pair
    # incremental → skip_unchanged（命名不同）；仅 incremental=True 时写入
    assert app.raw["incremental"]["skip_unchanged"] == config.incremental
    assert app.raw["incremental"]["since_date"] == config.since_date
    assert app.raw["updates"]["enabled"] == config.monitor_same_url


# ---- 输出（含 list→多 bool 转换）----
def test_output_formats_mapped(round_trip_pair):
    config, app = round_trip_pair
    outputs = app.raw["outputs"]
    # output_formats=["jsonl","parquet"] → outputs.jsonl=True, outputs.parquet=True
    assert outputs["jsonl"] is True
    assert outputs["parquet"] is True
    # 未选格式：to_yaml 写 False，deep_merge 覆盖 DEFAULTS 的 True
    assert outputs["csv"] is False
    assert outputs["xlsx"] is False
    assert outputs["duckdb"] is False


# ---- AI 配置（含 api_key 存在性断言）----
def test_ai_fields_mapped(round_trip_pair):
    config, app = round_trip_pair
    assert app.raw["ai"]["mode"] == config.ai_mode
    assert app.raw["ai"]["default_provider"] == config.ai_provider
    # providers 仅 ai_mode≠disabled 且 ai_provider 非空时写入；fixture 满足
    provider = app.raw["ai"]["providers"][config.ai_provider]
    assert provider["base_url"] == config.ai_base_url
    assert provider["model"] == config.ai_model
    # ai_api_key_ref="" → to_yaml 不写 api_key 键（config_serializer.py:179-180）
    # 注意：若设 secret:// 串，resolve_secret_refs 会反向解析为明文，不可等值断言
    assert "api_key" not in provider
    # extraction 子段
    extraction = app.raw["ai"]["extraction"]
    assert extraction["mode"] == config.extraction_mode
    assert extraction["prompt"] == config.ai_extraction_prompt
    assert extraction["chunk_strategy"] == config.ai_chunk_strategy
    assert extraction["max_tokens_per_chunk"] == config.ai_max_tokens_per_chunk


# ---- 资源 ----
def test_resource_profile_mapped(round_trip_pair):
    config, app = round_trip_pair
    assert app.raw["resources"]["profile"] == config.resource_profile


# ---- P9-B4（B05-006）：secret:// 引用环检测 ----
def test_resolve_secret_refs_cycle_detection(monkeypatch) -> None:
    """B05-006：secret:// 引用环必须被检测（不无限递归）。"""
    from omnicrawl.core import credentials

    def fake_get(name: str) -> str:
        if name == "A":
            return "secret://B"
        if name == "B":
            return "secret://A"
        raise AssertionError("unexpected")

    monkeypatch.setattr(credentials, "get_secret", fake_get)
    with pytest.raises(ValueError, match="形成环"):
        credentials.resolve_secret_refs("secret://A")


def test_resolve_secret_refs_self_reference_detected(monkeypatch) -> None:
    from omnicrawl.core import credentials

    monkeypatch.setattr(credentials, "get_secret", lambda name: "secret://A")
    with pytest.raises(ValueError, match="形成环"):
        credentials.resolve_secret_refs("secret://A")
