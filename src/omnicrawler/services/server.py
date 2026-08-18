from __future__ import annotations

import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ..core.config import AppConfig
from ..state import StateStore


def serve(config: AppConfig, host: str = "127.0.0.1", port: int = 8765) -> None:
    """只读状态监控服务。

    B08-010：默认仅绑定 localhost，只读（/api/status 等值比较、无文件服务、无 POST）。
    请勿以 ``0.0.0.0`` 暴露到远程——本服务无认证；如需远程访问，必须前置 TLS/令牌
    反代（如 Caddy/nginx），否则任何能到达该端口的人都能读取任务状态。
    """
    database = config.workspace / "state.sqlite3"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            if self.path == "/api/status":
                payload = _status_payload(config, database)
                body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path != "/":
                self.send_error(404)
                return
            body = _page(config).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:
            return

    print(f"OmniCrawler监控面板: http://{host}:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


def _status_payload(config: AppConfig, database) -> dict:
    if not database.exists():
        return {"project": config.project_name, "status": "not_started", "workspace": str(config.workspace)}
    with StateStore(database) as state:
        return {"project": config.project_name, "latest_run": state.latest_run(), "totals": state.stats()}


def _page(config: AppConfig) -> str:
    name = html.escape(config.project_name)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{name} · OmniCrawler</title>
<style>body{{font-family:system-ui,sans-serif;background:#f4f7fb;color:#132238;margin:0}}main{{max-width:960px;margin:40px auto;padding:0 20px}}.card{{background:white;border-radius:16px;padding:24px;box-shadow:0 8px 30px #18315314}}h1{{margin-top:0}}pre{{white-space:pre-wrap;background:#0e1b2b;color:#d8e7ff;padding:20px;border-radius:12px;min-height:260px}}.muted{{color:#607086}}</style></head>
<body><main><div class="card"><h1>{name}</h1><p class="muted">只读运行监控 · 每5秒刷新</p><pre id="status">正在读取状态…</pre></div></main>
<script>async function refresh(){{let r=await fetch('/api/status');document.getElementById('status').textContent=JSON.stringify(await r.json(),null,2)}}refresh();setInterval(refresh,5000)</script></body></html>"""

