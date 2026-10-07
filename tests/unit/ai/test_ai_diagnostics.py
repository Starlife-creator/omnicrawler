import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from omnicrawler.core.errors import PolicyBlockedError
from omnicrawler.services.ai_diagnostics import discover, estimate
from omnicrawler.services.ai_diagnostics import test_generation as generate_probe
from omnicrawler.services.ai_providers import build_provider


@pytest.fixture
def endpoint():
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            requests.append(("GET", self.path, None))
            self.reply({"data": [{"id": "fixture"}]})
        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(("POST", self.path, body))
            self.reply({"choices": [{"message": {"content": '{"ok":1}' if body["model"] == "numeric-boolean" else '{"ok":true}'}}],
                        "usage": {"prompt_tokens": 2, "completion_tokens": 5, "total_tokens": 7}})
        def reply(self, value):
            data = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/v1", requests
    server.shutdown()
    server.server_close()
    thread.join(5)


def test_discovery_never_generates_and_bounded_probe_verifies_schema(endpoint, tmp_path):
    base, requests = endpoint
    assert discover(base, "", tmp_path, allow_private=True) == ["fixture"]
    assert [row[0] for row in requests] == ["GET"]
    pricing = {"input_per_million": 1, "output_per_million": 2}
    result = generate_probe(base, "", "fixture", tmp_path, pricing=pricing, maximum_cost=.001,
                            allow_private=True, structured=True)
    assert result["status"] == "passed" and result["accounting"]["network_attempts"] == 1
    payload = requests[-1][2]
    assert payload["max_tokens"] == 16 and payload["response_format"]["type"] == "json_schema"
    assert result["accounting"]["billing_verified"] is False


def test_budget_unknown_pricing_and_private_default_block_before_network(endpoint, tmp_path):
    base, requests = endpoint
    with pytest.raises(ValueError, match="缺少模型单价"):
        generate_probe(base, "", "fixture", tmp_path, pricing={}, maximum_cost=.001)
    assert requests == []
    with pytest.raises(PolicyBlockedError):
        discover(base, "", tmp_path)
    assert requests == []
    assert estimate({})["estimated_cost"] is None


def test_legacy_ui_cost_limit_is_effective_in_common_provider():
    provider = build_provider({"mode": "enabled", "default_provider": "default",
        "providers": {"default": {"base_url": "https://example.org/v1", "model": "fixture"}},
        "budget": {"max_cost": .125}})
    assert provider.budget.maximum_cost == .125


def test_generation_rejects_numeric_value_in_boolean_schema(endpoint, tmp_path):
    base, requests = endpoint
    with pytest.raises(ValueError, match="固定验收内容校验"):
        generate_probe(base, "", "numeric-boolean", tmp_path, pricing={"input_per_million": 1, "output_per_million": 2},
                       maximum_cost=.001, allow_private=True, structured=True)
    assert len(requests) == 1 and requests[0][0] == "POST"
