from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.plugins.plugin_broker import CapabilityBroker, CapabilityError
from omnicrawler.plugins.plugin_declarative import validate_view_descriptor
from omnicrawler.plugins.plugin_render import RenderBroker
from omnicrawler.plugins.plugin_resources import ResourceGrantBroker
from omnicrawler.plugins.plugin_subprocess_adapter import (
    SubprocessResourceProviderAdapter,
    SubprocessViewAdapter,
)


def test_resource_grant_never_exposes_root_and_blocks_escape(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    (root / "image.png").write_bytes(b"png")
    broker = ResourceGrantBroker()
    handle = broker.grant_directory(root, label="Library")

    assert broker.describe(handle) == {"handle": handle, "label": "Library", "kind": "directory"}
    assert broker.enumerate(handle)[0]["relative"] == "image.png"
    assert broker.read(handle, "image.png") == b"png"
    assert str(root) not in repr(broker.describe(handle))
    with pytest.raises(ValueError, match="非法"):
        broker.read(handle, "../outside.txt")


def test_steam_discovery_is_scoped_and_returns_opaque_grant(tmp_path: Path, monkeypatch) -> None:
    program_files = tmp_path / "Program Files (x86)"
    workshop = program_files / "Steam" / "steamapps" / "workshop" / "content" / "431960"
    workshop.mkdir(parents=True)
    monkeypatch.setenv("ProgramFiles(x86)", str(program_files))
    monkeypatch.delenv("ProgramFiles", raising=False)
    broker = ResourceGrantBroker()

    handle = broker.discover_directory("steam_workshop", "431960")
    assert handle.startswith("resource:")
    assert "Steam Workshop" in broker.describe(handle)["label"]
    with pytest.raises(ValueError, match="app_id"):
        broker.discover_directory("steam_workshop", "../431960")


def test_view_validation_normalizes_layout_and_rejects_duplicate_ids() -> None:
    raw = {
        "view_id": "example.main", "title": "Example", "preferred_zone": "bottom",
        "components": [
            {"type": "directory_picker", "id": "library", "label": "Discover",
             "discovery_kind": "steam_workshop", "discovery_id": "431960"},
        ],
    }
    assert validate_view_descriptor(raw)["components"][0]["discovery_id"] == "431960"
    raw["components"].append({"type": "label", "id": "library", "text": "duplicate"})
    with pytest.raises(ValueError, match="不能重复"):
        validate_view_descriptor(raw)


class _Surface:
    def __init__(self) -> None:
        self.config: dict = {}

    def configure(self, payload: dict) -> dict:
        self.config = payload
        return {"active": True}

    @staticmethod
    def capabilities() -> dict:
        return {"version": 2, "input_passthrough": True}


def test_surface_capability_requires_permission() -> None:
    surface = _Surface()
    denied = CapabilityBroker(permissions=set(), system_info={}, surface_service=surface)
    with pytest.raises(CapabilityError, match="surfaces:background"):
        denied.dispatch("surface.background.configure", {"opacity": 20})
    allowed = CapabilityBroker(
        permissions={"surfaces:background"}, system_info={}, surface_service=surface
    )
    assert allowed.dispatch("surface.background.configure", {"opacity": 20})["active"]
    assert surface.config == {"opacity": 20}
    capabilities = allowed.dispatch("surface.background.capabilities", {})
    assert capabilities == {"version": 2, "input_passthrough": True}


def test_scripted_render_needs_second_permission() -> None:
    class Renderer:
        def snapshot_html(self, *args, **kwargs):
            return {"handle": "render:test"}

    broker = CapabilityBroker(
        permissions={"render:local"}, system_info={}, resource_broker=object(),
        render_broker=Renderer(),
    )
    with pytest.raises(CapabilityError, match="render:scripted"):
        broker.dispatch("render.html.snapshot", {"scripted": True})


def test_render_broker_uses_bounded_local_bytes_and_opaque_output(tmp_path: Path) -> None:
    root = tmp_path / "web"
    root.mkdir()
    (root / "index.html").write_text("<h1>Local</h1>", encoding="utf-8")
    (root / "style.css").write_text("h1 { color: red; }", encoding="utf-8")
    resources = ResourceGrantBroker()
    resource_handle = resources.grant_directory(root)

    class Runtime:
        def snapshot(self, html, **options):
            assert html == "<h1>Local</h1>"
            assert options["width"] == 800
            assert options["height"] == 600
            assert options["scripted"] is False
            assert options["base_url"] == "https://plugin-resource.invalid/"
            assert callable(options["asset_loader"])
            asset, content_type = options["asset_loader"]("style.css")
            assert asset == b"h1 { color: red; }"
            assert content_type == "text/css"
            return b"png-bytes"

    renderer = RenderBroker(Runtime())
    output = renderer.snapshot_html(
        resources, resource_handle, "index.html", width=800, height=600
    )
    assert output["handle"].startswith("render:")
    assert renderer.read_output(output["handle"]) == b"png-bytes"


def test_live_render_is_single_bounded_opaque_stream(tmp_path: Path) -> None:
    root = tmp_path / "web"
    root.mkdir()
    (root / "index.html").write_text("<script>tick()</script>", encoding="utf-8")
    resources = ResourceGrantBroker()
    resource_handle = resources.grant_directory(root)

    class Runtime:
        def stream(self, html, *, width, height, stop, on_frame, base_url, asset_loader):
            assert (html, width, height) == ("<script>tick()</script>", 1280, 720)
            assert base_url == "https://plugin-resource.invalid/"
            assert callable(asset_loader)
            on_frame(b"first-frame")
            stop.wait(1)

    renderer = RenderBroker(Runtime())
    output = renderer.start_html_live(resources, resource_handle, "index.html")
    assert output["fps_limit"] == 5
    assert renderer.is_live(output["handle"])
    assert renderer.read_output(output["handle"]) == b"first-frame"
    renderer.stop_live()
    assert not renderer.is_live(output["handle"])


def test_contract2_resource_and_view_adapters_use_data_only_operations() -> None:
    class Host:
        calls: list[tuple[str, dict]] = []

        def call(self, operation, payload):
            self.calls.append((operation, payload))
            if operation == "view.describe":
                return {"view": {
                    "view_id": "example.main", "title": "Example",
                    "components": [{"type": "label", "id": "status", "text": "Ready"}],
                }}
            return {"count": 0}

        def grant_directory(self, path, *, label=""):
            return "resource:test"

        def bind_surface(self, service):
            self.surface = service

        def close(self):
            pass

    host = Host()
    view = SubprocessViewAdapter(host)
    resources = SubprocessResourceProviderAdapter(host)
    assert view.describe()["preferred_zone"] == "right"
    assert resources.inventory()["count"] == 0
    assert [operation for operation, _payload in host.calls] == [
        "view.describe", "resource.inventory",
    ]


def test_text_component_validation() -> None:
    """U4（2026-09-24）：单行文本输入组件的描述符校验。

    正例：label/value/placeholder/maxlength 全量提供，规范化通过；
    反例：maxlength 越界 / 非整数 / value 超长 / 未知字段必须拒绝。
    """
    raw = {
        "view_id": "text-demo", "title": "代理配置",
        "components": [{
            "type": "text", "id": "proxy_url", "label": "机构代理 URL",
            "value": "http://proxy.example:8080", "placeholder": "http://…",
            "maxlength": 512,
        }],
    }
    normalized = validate_view_descriptor(raw)["components"][0]
    assert normalized["value"] == "http://proxy.example:8080"
    assert normalized["placeholder"] == "http://…"
    assert normalized["maxlength"] == 512

    # 缺省：value 为空串、maxlength 回落 512、placeholder 不出现
    minimal = validate_view_descriptor({
        "view_id": "text-min", "title": "x",
        "components": [{"type": "text", "id": "login_url"}],
    })["components"][0]
    assert minimal["value"] == "" and minimal["maxlength"] == 512
    assert "placeholder" not in minimal

    def _reject(component: dict, match: str = "") -> None:
        bad = {"view_id": "text-bad", "title": "x", "components": [component]}
        with pytest.raises(ValueError, match=match):
            validate_view_descriptor(bad)

    _reject({"type": "text", "id": "comp", "maxlength": 0}, "超出范围")
    _reject({"type": "text", "id": "comp", "maxlength": 513}, "超出范围")
    _reject({"type": "text", "id": "comp", "maxlength": "long"}, "必须是整数")
    _reject({"type": "text", "id": "comp", "value": "x" * 513}, "过长")
    _reject({"type": "text", "id": "comp", "evil_field": 1}, "未知字段")


def test_view_progress_relay_routing_and_silent_degradation() -> None:
    """U6（2026-09-24）：view.progress 按 plugin_id 路由投递；异常面全部静默降级。

    正例：绑了 relay → delivered=True，payload 被净化并带上 plugin_id；
    反例：无面板 → no-view-panel；relay 抛错 → relay-error 不炸宿主；
    未知字段 / 非整数 done → E_CONTRACT 拒绝（协议纪律与其它能力同源）。
    """
    received: list[dict] = []
    broker = CapabilityBroker(
        permissions=set(), system_info={}, plugin_id="paper-dl",
        progress_relay=received.append,
    )
    assert broker.dispatch(
        "view.progress", {"done": 3, "total": 10, "current_doi": "10.1234/abc"}
    ) == {"delivered": True}
    assert received[0]["done"] == 3
    assert received[0]["plugin_id"] == "paper-dl"

    no_panel = CapabilityBroker(permissions=set(), system_info={}, plugin_id="p")
    assert no_panel.dispatch("view.progress", {"done": 1, "total": 2}) == {
        "delivered": False, "reason": "no-view-panel",
    }

    def _boom(_payload: dict) -> None:
        raise RuntimeError("gui gone")

    broken = CapabilityBroker(permissions=set(), system_info={}, progress_relay=_boom)
    assert broken.dispatch("view.progress", {"done": 1, "total": 2}) == {
        "delivered": False, "reason": "relay-error",
    }

    with pytest.raises(CapabilityError, match="未知字段"):
        broker.dispatch("view.progress", {"done": 1, "evil": 1})
    with pytest.raises(CapabilityError, match="必须是整数"):
        broker.dispatch("view.progress", {"done": "soon"})
    # 无需 manifest 权限（与 system.info 同为内置）
    assert CapabilityBroker(permissions=set(), system_info={}).dispatch(
        "view.progress", {}
    )["delivered"] is False


def test_progress_component_rejects_static_values() -> None:
    """U6：progress 组件字段面收紧到 type/id/label——数值只走推送，不接受静态值。"""
    ok = validate_view_descriptor({
        "view_id": "prog-demo", "title": "x",
        "components": [{"type": "progress", "id": "bar", "label": "下载进度"}],
    })
    assert ok["components"][0]["type"] == "progress"
    with pytest.raises(ValueError, match="未知字段"):
        validate_view_descriptor({
            "view_id": "prog-bad", "title": "x",
            "components": [{"type": "progress", "id": "bar", "value": 42}],
        })


def test_rich_text_component_validation() -> None:
    """P2.1（2026-09-24）：受限长文本组件的描述符校验。

    正例：四类段（heading/paragraph/bullet/link）规范化通过；
    反例：未知段类型 / 非 http(s) URL / 空 segments / 未知字段 / 超总长。
    """
    from omnicrawler.plugins.plugin_declarative import validate_view_descriptor

    raw = {
        "view_id": "rich-demo", "title": "富文本",
        "components": [{
            "type": "rich_text", "id": "body",
            "segments": [
                {"type": "heading", "text": "标题"},
                {"type": "paragraph", "text": "正文段落"},
                {"type": "bullet", "text": "条目"},
                {"type": "link", "text": "官网", "url": "https://example.com"},
            ],
        }],
    }
    normalized = validate_view_descriptor(raw)
    segments = normalized["components"][0]["segments"]
    assert [s["type"] for s in segments] == ["heading", "paragraph", "bullet", "link"]
    assert segments[3]["url"] == "https://example.com"

    def _reject(mutate) -> None:
        bad = {
            "view_id": "rich-bad", "title": "x",
            "components": [{"type": "rich_text", "id": "body", "segments": [
                {"type": "heading", "text": "h"},
            ]}],
        }
        mutate(bad["components"][0]["segments"])
        with pytest.raises(ValueError):
            validate_view_descriptor(bad)

    _reject(lambda s: s.append({"type": "script", "text": "x"}))          # 未知段类型
    _reject(lambda s: s.append({"type": "link", "text": "x", "url": "javascript:alert(1)"}))  # 非 http(s)
    _reject(lambda s: s.append({"type": "link", "text": "x"}))            # link 缺 url
    _reject(lambda s: s.append({"type": "paragraph", "text": "x", "evil": 1}))  # 未知字段
    with pytest.raises(ValueError, match="非空数组"):
        validate_view_descriptor({
            "view_id": "rich-empty", "title": "x",
            "components": [{"type": "rich_text", "id": "body", "segments": []}],
        })
