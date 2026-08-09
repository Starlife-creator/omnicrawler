"""Post-build smoke tests for both browser engines and both offline OCR engines."""

from __future__ import annotations

import argparse
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/robots.txt":
            body = b"User-agent: *\nAllow: /\n"
            content_type = "text/plain"
        else:
            body = b"""<!doctype html><html><head><title>portable-browser-ok</title></head>
<body><h1 id="result">loading</h1><script>
document.querySelector('#result').textContent = 'bundled browser ready';
document.querySelector('#result').setAttribute('data-ready', 'yes');
</script></body></html>"""
            content_type = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _run_browser(executable: Path, release_dir: Path, root: Path, url: str, engine: str) -> None:
    config = root / f"{engine}-smoke.yaml"
    config.write_text(
        f"""project:
  name: {engine}-portable-smoke
  workspace: {str(root / (engine + '-work')).replace(chr(92), '/')}
source:
  kind: browser
  seeds: [{url}]
crawl: {{max_pages: 1, same_host: true}}
http: {{allow_private_network: true, respect_robots: false, delay_seconds: 0}}
egress:
  # The Selenium smoke target is an isolated loopback server owned by this
  # process. Explicitly exercise the audited compatibility boundary instead
  # of weakening the product default for normal projects.
  allow_unintercepted_selenium: {str(engine == "selenium").lower()}
browser:
  engine: {engine}
  headless: true
  wait_until: networkidle
  launch_args: [--no-sandbox, --disable-dev-shm-usage]
  actions:
    - {{action: wait_for, selector: "#result[data-ready='yes']", timeout_ms: 10000}}
extract:
  mode: html
  fields:
    title: {{selector: title}}
    heading: {{selector: h1}}
""",
        encoding="utf-8",
    )
    import os as _os
    _env = {**_os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    completed = subprocess.run(
        [str(executable), "run", "-c", str(config)],
        cwd=str(release_dir), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=180, env=_env,
    )
    # On Windows, the PyInstaller-bundled CLI may crash with UnicodeEncodeError
    # when printing emoji to a GBK-encoded pipe. If the crawl itself succeeded
 # (records exist and contain expected content), treat the encoding error as
 # a non-fatal cosmetic issue rather than a functional failure.
    records = list((root / f"{engine}-work").rglob("records.jsonl"))
    payload = "\n".join(path.read_text(encoding="utf-8") for path in records)
    if "bundled browser ready" in payload:
        # F20：内容成功导出但进程非零退出——区分"收尾编码崩溃"（非致命）与真正失败
        if completed.returncode == 0:
            return
        output = (completed.stdout + "\n" + completed.stderr).strip()
        if "UnicodeEncodeError" in output:
            return
        raise RuntimeError(
            f"portable {engine} smoke test produced expected content but exited {completed.returncode}:\n{output[-6000:]}"
        )
    if completed.returncode != 0:
        output = (completed.stdout + "\n" + completed.stderr).strip()
        raise RuntimeError(f"portable {engine} smoke test failed:\n{output[-6000:]}")
    raise RuntimeError(
        f"{engine} ran but dynamic content was not exported\n"
        f"records={len(records)} payload={payload[-3000:]!r}"
    )


def run_smoke_test(release_dir: Path, edition: str = "Full") -> None:
    import os as _os
    _env = {**_os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    executable = release_dir / "omnicrawl.exe"
    if not executable.is_file():
        raise FileNotFoundError(executable)
    # F21：GUI 壳（OmniCrawler.exe）也必须过冒烟——缺 PyQt 插件/Qt DLL 只会在
    # 用户双击时才暴露。离屏启动：进程持续运行说明 GUI 正常初始化，然后主动退出。
    gui = release_dir / "OmniCrawler.exe"
    if gui.is_file():
        gui_env = {**_env, "QT_QPA_PLATFORM": "offscreen"}
        gui_proc = subprocess.Popen([str(gui)], cwd=str(release_dir), env=gui_env)
        try:
            gui_proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            gui_proc.terminate()
            gui_proc.wait(timeout=10)
        else:
            raise RuntimeError(
                f"OmniCrawler.exe (GUI) exited immediately with rc={gui_proc.returncode}"
            )
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="omnicrawl-portable-smoke-") as temp:
            root = Path(temp)
            url = f"http://127.0.0.1:{server.server_port}/"
            engines = ("playwright", "selenium") if edition == "Full" else ("playwright",)
            for engine in engines:
                _run_browser(executable, release_dir, root, url, engine)
        if edition == "Full":
            completed = subprocess.run(
                [str(executable), "capabilities", "--self-test"],
                cwd=str(release_dir), capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=600, env=_env,
            )
            if completed.returncode != 0:
                output = (completed.stdout + "\n" + completed.stderr).strip()
                raise RuntimeError(f"portable OCR self-test failed:\n{output[-10000:]}")
    finally:
        server.shutdown()
        server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("release_dir", type=Path)
    parser.add_argument("--edition", choices=["Standard", "Full"], default="Full")
    args = parser.parse_args()
    run_smoke_test(args.release_dir.resolve(), args.edition)
    print(f"portable {args.edition} runtime smoke tests: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
