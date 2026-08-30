"""Phase 2b loader 接线契约测试：daily_quota/egress_policy 配置解析生效。"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.plugins.plugin_subprocess_adapter import _SubprocessSessionHost
from omnicrawler.plugins.plugins import (
    SIGNATURE_POLICY_DEVELOPER,
    Registry,
    load_local_plugins,
)

pytestmark = pytest.mark.plugin_contract


def _build_contract2_plugin(
    root: Path,
    name: str,
    *,
    permissions: list[str],
    plugin_types: list[str] | None = None,
) -> Path:
    plugin_dir = root / "plugins" / name
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.py").write_text(
        f"PLUGIN_METADATA = {{'name': {name!r}, 'version': '1.0', "
        f"'execution_mode': 'subprocess', 'plugin_types': {plugin_types or ['source']!r}, "
        f"'permissions': {permissions!r}}}\n"
        "def handle(operation, payload):\n"
        "    if operation == 'source.seed':\n"
        "        return {'requests': [{'url': 'https://example.com/'}]}\n"
        "    if operation == 'fetcher.fetch':\n"
        "        request = payload['request']\n"
        "        return {'url': request['url'], 'status': 202, "
        "'headers': {'Content-Type': 'text/plain'}, 'body_b64': 'b2s='}\n"
        "    if operation in {'processor.process', 'parser.process', 'extractor.process'}:\n"
        "        return {'records': [{'source_url': payload['result']['final_url'], "
        "'record_type': operation.split('.')[0], 'data': {'ok': True}}], 'requests': []}\n"
        "    if operation == 'auth.prepare':\n"
        "        request = payload['request']\n"
        "        request['headers']['X-Saw-Authorization'] = "
        "request['headers'].get('Authorization', '')\n"
        "        request['headers']['X-Plugin-Auth'] = 'applied'\n"
        "        return {'request': request}\n"
        "    if operation == 'transformer.transform':\n"
        "        data = dict(payload['record']['data'])\n"
        "        data['transformed'] = True\n"
        "        return {'data': data}\n"
        "    if operation == 'exporter.export':\n"
        "        return {'run_id': payload['run_id'], 'exported': True}\n"
        "    if operation.startswith('hook.'):\n"
        "        return {'event': operation, 'run_id': payload.get('run_id')}\n"
        "    return {'operation': operation}\n",
        encoding="utf-8",
    )
    return plugin_dir


def test_loader_wires_daily_quota_from_config(tmp_path: Path) -> None:
    """plugins.network_daily_quota 按 plugin_id 解析 → host 注入配额。"""
    plugin_dir = _build_contract2_plugin(tmp_path, "quota_demo", permissions=["network:scoped"])
    plugins_section = {"network_daily_quota": {"quota_demo": {"requests": 1}}}
    from omnicrawler.plugins.plugin_router import detect_contract_shape
    from omnicrawler.plugins.plugins import _static_plugin_metadata

    source = (plugin_dir / "plugin.py").read_text(encoding="utf-8")
    meta = _static_plugin_metadata(plugin_dir / "plugin.py", source)
    assert detect_contract_shape(source) == 2
    assert meta is not None

    # 模拟 _load_local_plugin 的 host 构造段
    from omnicrawler.plugins.plugin_quota import DailyNetworkQuota

    quota_rules = plugins_section.get("network_daily_quota", {}) or {}
    daily_quota = DailyNetworkQuota({meta.name: quota_rules[meta.name]})
    host = _SubprocessSessionHost(
        plugin_dir, "plugin",
        permissions=set(), config=None, plugin_id=meta.name,
        daily_quota=daily_quota,
        egress_policy="prompt",
    )
    assert host._daily_quota is not None
    broker = host._ensure()[1]
    assert broker._daily_quota is not None
    host.close()


def test_loader_wires_egress_policy_block(tmp_path: Path) -> None:
    """plugins.egress_policy=block → host 注入 block 档。"""
    plugin_dir = _build_contract2_plugin(tmp_path, "egress_demo", permissions=["network:scoped"])
    host = _SubprocessSessionHost(
        plugin_dir, "plugin", permissions=set(), config=None,
        plugin_id="egress_demo", egress_policy="block",
    )
    broker = host._ensure()[1]
    assert broker._egress_policy == "block"
    host.close()


def test_loader_end_to_end_contract2_still_loads(tmp_path: Path) -> None:
    """Phase 2b 参数接入后契约 2 插件端到端加载不回归。"""
    _build_contract2_plugin(tmp_path, "c2_demo", permissions=[])
    registry = Registry()
    load_local_plugins(
        registry, ["plugins/"], tmp_path,
        signature_policy=SIGNATURE_POLICY_DEVELOPER,
        fail_open=False,
    )
    assert "c2_demo" in registry.sources
    adapter = registry.sources["c2_demo"](None)
    requests = adapter.seed()
    assert requests and requests[0].url == "https://example.com/"
    adapter.close()


def test_loader_wires_contract2_fetcher(tmp_path: Path) -> None:
    """fetcher 已有 adapter 时必须真正注册到 Registry，而不只是接受元数据。"""
    from omnicrawler.core.models import CrawlRequest

    _build_contract2_plugin(
        tmp_path,
        "fetch_demo",
        permissions=[],
        plugin_types=["fetcher"],
    )
    registry = Registry()
    load_local_plugins(
        registry,
        ["plugins/"],
        tmp_path,
        signature_policy=SIGNATURE_POLICY_DEVELOPER,
        fail_open=False,
    )
    assert "fetch_demo" not in registry.sources
    assert "fetch_demo" in registry.fetchers
    adapter = registry.fetchers["fetch_demo"](None)
    result = adapter.fetch(CrawlRequest(url="https://example.com/data"))
    assert result.status == 202
    assert result.body == b"ok"
    assert result.final_url == "https://example.com/data"
    adapter.close()


def test_loader_wires_pipeline_extension_adapters(tmp_path: Path) -> None:
    """除原生 UI 外的正式 adapter 必须进入 Registry 并可通过隔离会话调用。"""
    from omnicrawler.core.models import CrawlRequest, ExtractedRecord, FetchResult
    from omnicrawler.plugins.plugin_runtime import (
        prepare_request,
        run_exporter,
        transform_record,
    )

    _build_contract2_plugin(
        tmp_path,
        "pipeline_demo",
        permissions=[],
        plugin_types=[
            "processor",
            "parser",
            "extractor",
            "auth_provider",
            "transformer",
            "exporter",
            "hook",
        ],
    )
    registry = Registry()
    load_local_plugins(
        registry,
        ["plugins/"],
        tmp_path,
        signature_policy=SIGNATURE_POLICY_DEVELOPER,
        fail_open=False,
    )
    assert "pipeline_demo" in registry.processors
    assert "pipeline_demo" in registry.parsers
    assert "pipeline_demo" in registry.extractors
    assert "pipeline_demo" in registry.auth_providers
    assert "pipeline_demo" in registry.transformers
    assert "pipeline_demo" in registry.exporters
    assert "before_run" in registry.hooks
    request = CrawlRequest(
        "https://example.com/",
        headers={"Authorization": "Bearer existing"},
        body=b"signed-body",
    )
    result = FetchResult(request, request.url, 200, {}, b"ok", 0.1)
    processor = registry.processors["pipeline_demo"](None, {})
    assert processor.process(result).records[0].data == {"ok": True}
    parser = registry.parsers["pipeline_demo"](None, {})
    assert parser.process(result).records[0].record_type == "parser"
    extractor = registry.extractors["pipeline_demo"](None, {})
    assert extractor.process(result).records[0].record_type == "extractor"
    auth = registry.auth_providers["pipeline_demo"](None, {})
    prepared = prepare_request(auth, request)
    assert prepared.headers["X-Plugin-Auth"] == "applied"
    assert prepared.headers["X-Saw-Authorization"] == "<redacted>"
    assert prepared.headers["Authorization"] == "Bearer existing"
    assert prepared.body == b"signed-body"
    transformer = registry.transformers["pipeline_demo"](None, {})
    record = ExtractedRecord(request.url, "demo", {"ok": True})
    assert transform_record(transformer, record).data["transformed"] is True
    exported = run_exporter(
        registry.exporters["pipeline_demo"], None, object(), "run-3", {}
    )
    assert exported == {"run_id": "run-3", "exported": True}
    assert registry.emit("before_run", run_id="run-3")[0]["event"] == "hook.before_run"
    registry.close()


def test_loader_rejects_unknown_runtime_plugin_type(tmp_path: Path) -> None:
    """自由业务分类不能伪装成宿主运行扩展点。"""
    _build_contract2_plugin(
        tmp_path,
        "unknown_demo",
        permissions=[],
        plugin_types=["academic_ai"],
    )
    with pytest.raises(ValueError, match="未知运行扩展点"):
        load_local_plugins(
            Registry(),
            ["plugins/"],
            tmp_path,
            signature_policy=SIGNATURE_POLICY_DEVELOPER,
            fail_open=False,
        )
