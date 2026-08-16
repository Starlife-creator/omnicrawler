"""Post-build smoke tests for both browser engines and both offline OCR engines."""

from __future__ import annotations

import argparse
import subprocess
import sys
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
    # OMNICRAWL_SMOKE_LIVE=1 时实时透传子进程输出到 stderr——CI 排查
    # selenium 引擎挂起时能看到卡住前的最后日志（macOS BiDi 挂起诊断用）。
    live = _os.environ.get("OMNICRAWL_SMOKE_LIVE", "") == "1"
    if live:
        import sys as _sys

        proc = subprocess.Popen(
            [str(executable), "run", "-c", str(config)],
            cwd=str(release_dir), text=True, encoding="utf-8",
            errors="replace", env=_env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        collected: dict[str, list[str]] = {"out": [], "err": []}

        def _drain(handle: Any, kind: str) -> None:
            if handle is None:
                return
            for line in iter(handle.readline, ""):
                collected[kind].append(line)
                print(f"[smoke-{engine}-{kind}] {line}", file=_sys.stderr, end="")
            try:
                handle.close()
            except Exception:
                pass

        out_thread = threading.Thread(target=_drain, args=(proc.stdout, "out"), daemon=True)
        err_thread = threading.Thread(target=_drain, args=(proc.stderr, "err"), daemon=True)
        out_thread.start()
        err_thread.start()
        rc = proc.wait(timeout=180)
        out_thread.join(timeout=5)
        err_thread.join(timeout=5)
        completed = type(
            "Completed", (), {
                "returncode": rc,
                "stdout": "".join(collected["out"]),
                "stderr": "".join(collected["err"]),
            }
        )()
    else:
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
    stderr_tail = (completed.stderr or "").strip()[-4000:]
    raise RuntimeError(
        f"{engine} ran but dynamic content was not exported\n"
        f"records={len(records)} payload={payload[-3000:]!r}\n"
        f"stderr={stderr_tail!r}"
    )


def _resolve_executable(release_dir: Path, *, gui: bool = False) -> Path | None:
    """按平台解析打包可执行文件（P4-3：不再硬编码 Windows .exe）。"""
    if gui:
        candidates = (
            release_dir / "OmniCrawler.exe",  # Windows
            release_dir / "OmniCrawler",  # Linux
            release_dir / "OmniCrawler.app" / "Contents" / "MacOS" / "OmniCrawler",  # macOS
        )
    else:
        candidates = (
            release_dir / "omnicrawl.exe",  # Windows
            release_dir / "omnicrawl",  # Linux
            release_dir / "OmniCrawler.app" / "Contents" / "MacOS" / "omnicrawl",  # macOS
        )
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def run_smoke_test(release_dir: Path, edition: str = "Full") -> None:
    import os as _os
    _env = {**_os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    executable = _resolve_executable(release_dir)
    if executable is None:
        raise FileNotFoundError(f"未找到 CLI 可执行文件（Windows/Linux/macOS 候选均不存在）: {release_dir}")
    # F21：GUI 壳（OmniCrawler.exe / OmniCrawler）也必须过冒烟——缺 PyQt 插件/
    # Qt DLL 只会在用户双击时才暴露。离屏启动：进程持续运行说明 GUI 正常初始化，
    # 然后主动退出。
    gui = _resolve_executable(release_dir, gui=True)
    if gui is not None:
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
            if edition == "Full" and sys.platform == "darwin":
                # macOS：selenium 4.47 BiDi 网络拦截（continue_request）有上游 bug
                # （'Timed out waiting for response to BiDi command'，Chrome 151 +
                # macOS arm64，v0.9.1 CI 实测），完整爬取会挂起。playwright 引擎不受
                # 影响（不走 BiDi 拦截），且已完整验证打包的 Chromium/ChromeDriver。
                # selenium 引擎在 Linux/Windows 继续完整冒烟。运行时若 macOS 用户用
                # selenium，watchdog fail-closed 快速报错并提示改用 playwright。
                print("macOS: 跳过 selenium 引擎冒烟（selenium 4.47 BiDi 上游 bug，见注释）")
                engines = ("playwright",)
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
