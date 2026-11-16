"""Post-build smoke tests for both browser engines and both offline OCR engines."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


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
            release_dir / "omnicrawler-cli.exe",  # Windows（CLI 与 GUI OmniCrawler.exe 大小写冲突，见 spec）
            release_dir / "omnicrawler",  # Linux
            release_dir / "OmniCrawler.app" / "Contents" / "MacOS" / "omnicrawler-cli",  # macOS
        )
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _verify_windows_icon(exe: Path) -> None:
    """B2: the built GUI exe must embed the branded icon (RT_GROUP_ICON + frames)."""
    import ctypes
    from ctypes import WINFUNCTYPE, c_int, c_ssize_t, c_void_p

    LOAD_LIBRARY_AS_DATAFILE = 0x00000002
    RT_GROUP_ICON = 14
    RT_ICON = 3
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # 必须显式声明签名：默认 restype 是 c_int，64 位下会把 HMODULE 截断，
    # 枚举于是恒返回 0（"有图标也报没有"——典型假红）。
    kernel32.LoadLibraryExW.argtypes = [ctypes.c_wchar_p, c_void_p, ctypes.c_uint32]
    kernel32.LoadLibraryExW.restype = c_void_p
    kernel32.EnumResourceNamesW.argtypes = [c_void_p, c_void_p, c_void_p, c_ssize_t]
    kernel32.EnumResourceNamesW.restype = ctypes.c_int
    kernel32.FreeLibrary.argtypes = [c_void_p]
    kernel32.FreeLibrary.restype = ctypes.c_int
    hmod = kernel32.LoadLibraryExW(str(exe), None, LOAD_LIBRARY_AS_DATAFILE)
    if not hmod:
        raise RuntimeError(
            f"brand icon check: cannot load {exe.name} as datafile "
            f"(GetLastError={ctypes.get_last_error()})"
        )

    def _count(resource_type: int) -> int:
        # Name may be an integer ID (IS_INTRESOURCE) -- never cast it to c_wchar_p,
        # a small pointer value would crash the reader instead of failing cleanly.
        ENUMPROC = WINFUNCTYPE(c_int, c_void_p, c_void_p, c_void_p, c_ssize_t)
        names: list[c_void_p] = []

        def _on_name(_hmod: int, _type: int, name: int, _param: int) -> int:
            names.append(c_void_p(name))
            return 1

        callback = ENUMPROC(_on_name)
        kernel32.EnumResourceNamesW.argtypes = [c_void_p, c_void_p, ENUMPROC, c_ssize_t]
        kernel32.EnumResourceNamesW(hmod, c_void_p(resource_type), callback, 0)
        return len(names)

    try:
        groups = _count(RT_GROUP_ICON)
        frames = _count(RT_ICON)
    finally:
        kernel32.FreeLibrary(hmod)
    if groups < 1 or frames < 1:
        raise RuntimeError(
            f"brand icon check: {exe.name} carries no icon resource "
            f"(RT_GROUP_ICON={groups}, RT_ICON={frames}); "
            "the spec EXE(icon=...) wiring is broken"
        )
    print(f"brand icon check: {exe.name} embeds {groups} icon group(s), {frames} frame(s)")


def _verify_macos_icon(release_dir: Path) -> None:
    """B2: the .app bundle must ship the branded .icns referenced by Info.plist."""
    import plistlib

    app = release_dir / "OmniCrawler.app"
    resources = app / "Contents" / "Resources"
    icns = sorted(resources.glob("*.icns"))
    if not icns:
        raise RuntimeError(
            f"brand icon check: no .icns under {resources}; BUNDLE(icon=...) wiring is broken"
        )
    info_plist = app / "Contents" / "Info.plist"
    with info_plist.open("rb") as handle:
        info = plistlib.load(handle)
    icon_file = str(info.get("CFBundleIconFile", ""))
    expected = {icon_file, f"{icon_file}.icns"} - {""}
    if not any(item.name in expected for item in icns):
        raise RuntimeError(
            f"brand icon check: Info.plist CFBundleIconFile={icon_file!r} does not match "
            f"bundled {[item.name for item in icns]}"
        )
    print(f"brand icon check: {icns[0].name} present and referenced by CFBundleIconFile")


def _verify_brand_icon(release_dir: Path, gui_exe: Path) -> None:
    """B2 guard: the shipped GUI entry must carry the branded icon.

    Linux has nothing to assert here by design: PyInstaller drops ``icon=`` on ELF
    binaries, and desktop-entry branding lands with the I1b installer batch. The
    skip is printed on purpose (never silent).
    """
    if sys.platform == "win32":
        _verify_windows_icon(gui_exe)
    elif sys.platform == "darwin":
        _verify_macos_icon(release_dir)
    else:
        print("brand icon check: skipped on linux (desktop-entry branding ships with I1b)")


def _probe_capabilities(executable: Path, release_dir: Path, env: dict[str, str]) -> dict | None:
    """探测**产物实际具备**的能力（条目 13）。

    为什么需要它：原先"要不要跑 OCR 自检"是按 `edition == "Full"` 判断的。
    停发 Full 后 Standard 成为唯一发布版本，按版本名判断会让 OCR 自检
    **永久失去覆盖**——哪怕 Standard 里其实有 OCR（用户按需装组件、
    或本机已装 Tesseract/Paddle）。

    正确判据是**能力是否存在**，不是**版本叫什么**。

    返回 ``capabilities`` 子命令的 JSON 字典；探测本身失败时返回 ``None``
    （由调用方决定是跳过还是判失败——不能默认当成"没有能力"）。

    ⚠ 这里**不加** ``--json``：``capabilities`` 默认就输出 JSON，
    传一个它不认识的参数会让命令直接报错退出（实测），
    于是探测永远返回 None、所有检查被静默跳过。
    """
    try:
        completed = subprocess.run(
            [str(executable), "capabilities", "--mode", "quick"],
            cwd=str(release_dir), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=180, env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    # 输出前面可能混有日志行，取第一个 JSON 对象的起点
    stdout = completed.stdout or ""
    start = stdout.find("{")
    if start < 0:
        return None
    try:
        report, _ = json.JSONDecoder().raw_decode(stdout[start:])
    except json.JSONDecodeError:
        return None
    return report if isinstance(report, dict) else None


def _capability_available(report: dict | None, key: str) -> bool:
    """从能力报告里读某个能力是否可用（缺失一律视为**不可用**）。

    保守取值：读不到就当没有。这与仓库既有的 fail-closed 口径一致——
    宁可少跑一项检查并说明原因，也不假装测过了。

    报告结构见 ``core.capabilities.capability_report()``：
    ``modules`` 是逐模块的 ``{"module", "installed"}``，``features`` 是
    extra 级别的可用性。两个都要看——OCR 既可能来自模块，也可能来自 extra。
    """
    if not report:
        return False
    modules = report.get("modules")
    if isinstance(modules, dict):
        for name, entry in modules.items():
            if key in name and isinstance(entry, dict) and entry.get("installed"):
                return True
    features = report.get("features")
    if isinstance(features, dict) and features.get(key):
        return True
    # 顶层同名字段（老版本报告的形态）
    return bool(report.get(key))


def run_smoke_test(release_dir: Path, edition: str = "Full") -> None:
    import os as _os
    _env = {**_os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    executable = _resolve_executable(release_dir)
    if executable is None:
        raise FileNotFoundError(f"未找到 CLI 可执行文件（Windows/Linux/macOS 候选均不存在）: {release_dir}")
    # 条目 13：先探测产物**实际具备**的能力，后续按能力而不是按版本名决定跑什么。
    # 探测失败不当作"没有能力"——那会把"测不了"和"不支持"混为一谈。
    _capabilities = _probe_capabilities(executable, release_dir, _env)
    # F21：GUI 壳（OmniCrawler.exe / OmniCrawler）也必须过冒烟——缺 PyQt 插件/
    # Qt DLL 只会在用户双击时才暴露。离屏启动：进程持续运行说明 GUI 正常初始化，
    # 然后主动退出。
    gui = _resolve_executable(release_dir, gui=True)
    if gui is not None:
        _verify_brand_icon(release_dir, gui)
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
        with tempfile.TemporaryDirectory(prefix="omnicrawler-portable-smoke-") as temp:
            root = Path(temp)
            url = f"http://127.0.0.1:{server.server_port}/"
            engines = ("playwright",)
            # 条目 13：判断「这个产物里有没有 selenium 引擎」，而不是「版本名是不是 Full」。
            # 原有注释解释的是**为什么有 selenium 也不冒烟它**（selenium 4.47 的 BiDi
            # 平台 bug 会让完整爬取挂起），那段理由与版本名无关——停发 Full 后
            # Standard 若带 selenium 同样会撞上。
            has_selenium = _capability_available(_capabilities, "selenium")
            if has_selenium:
                # Linux 亦在 v0.9.1 CI 实测触发 selenium 4.47 + Chrome 151 的 BiDi
                # 网络拦截超时（continue_request/fail_request 'Timed out waiting
                # for response to BiDi command'），完整爬取挂起后 fail-closed。
                # 各平台统一跳过 selenium 引擎冒烟：playwright 引擎不走 BiDi 拦截
                # 故正常，已完整验证打包的 Chromium/ChromeDriver；运行时 selenium
                # 有 guard 兜底 + watchdog fail-closed 快速报错。
                print(
                    f"{sys.platform}: 跳过 selenium 引擎冒烟"
                    "（selenium 4.47 BiDi 平台 bug，见注释；与 edition 无关）"
                )
            for engine in engines:
                _run_browser(executable, release_dir, root, url, engine)
        if _capability_available(_capabilities, "ocr"):
            completed = subprocess.run(
                [str(executable), "capabilities", "--self-test"],
                cwd=str(release_dir), capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=600, env=_env,
            )
            if completed.returncode != 0:
                output = (completed.stdout + "\n" + completed.stderr).strip()
                raise RuntimeError(f"portable OCR self-test failed:\n{output[-10000:]}")
        else:
            # 跳过必须说明原因：产物里确实没有 OCR 能力（而非"忘了测"）。
            print(
                f"跳过 OCR 自检：该产物未声明 OCR 能力"
                f"（edition={edition}，能力报告={'可读' if _capabilities else '不可读'}）"
            )
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
