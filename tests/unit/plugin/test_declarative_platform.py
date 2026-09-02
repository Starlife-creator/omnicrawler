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
