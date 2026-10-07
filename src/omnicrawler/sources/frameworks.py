from __future__ import annotations

import json
import math
import os
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..core.config import AppConfig


def _stop_owned(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=10, check=False)
    else:
        os.killpg(process.pid, signal.SIGKILL)
    if process.poll() is None:
        process.kill()
    process.wait(timeout=10)


def _run_owned(command: list[str], *, cwd: Path, output: Path, logs: Path,
               timeout: float, maximum_bytes: int,
               should_stop: Callable[[], bool] | None = None) -> dict[str, Any]:
    started = time.monotonic()
    stdout, stderr = logs / "stdout.log", logs / "stderr.log"
    with stdout.open("wb") as out, stderr.open("wb") as err:
        process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                   start_new_session=os.name != "nt",
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            while process.poll() is None:
                if should_stop and should_stop():
                    raise InterruptedError("Scrapy cancelled")
                if time.monotonic() - started >= timeout:
                    raise TimeoutError("Scrapy execution timed out")
                if any(path.is_file() and path.stat().st_size > maximum_bytes for path in (output, stdout, stderr)):
                    raise ValueError("Scrapy output budget exceeded")
                time.sleep(0.05)
        finally:
            _stop_owned(process)
    def tail(path: Path) -> str:
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 4000))
            return stream.read().decode("utf-8", errors="replace")
    return {"returncode": process.returncode, "stdout_tail": tail(stdout), "stderr_tail": tail(stderr)}


def run_scrapy(config: AppConfig, *, should_stop: Callable[[], bool] | None = None) -> dict[str, Any]:
    source = config.section("source")
    spider = source.get("spider_file")
    if not spider:
        raise ValueError("Scrapy模式必须设置source.spider_file")
    spider_path = config.resolve(spider)
    if not spider_path.is_file():
        raise FileNotFoundError(f"Scrapy spider不存在: {spider_path}")
    if source.get("execution_contract") != "trusted_external":
        raise ValueError("Scrapy executes trusted Python outside native networking; set source.execution_contract: trusted_external")
    egress = config.section("egress")
    if egress.get("enabled", True) is False or any(egress.get(key) for key in (
        "allowed_domains", "allowed_ports", "maximum_requests", "maximum_bytes",
        "maximum_runtime_seconds", "maximum_concurrency", "maximum_cost",
    )):
        raise ValueError("Scrapy cannot enforce native egress restrictions; use a native source")
    timeout = source.get("timeout_seconds", 60)
    maximum = source.get("max_output_bytes", 50_000_000)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise ValueError("Scrapy timeout_seconds must be in (0,300]")
    if type(maximum) is not int or not 1 <= maximum <= 100_000_000:
        raise ValueError("Scrapy max_output_bytes must be in [1,100000000]")
    output = config.workspace / "output"
    output.mkdir(parents=True, exist_ok=True)
    result_path = output / "scrapy_records.jsonl"
    summary: dict[str, Any] = {
        "status": "failed", "output": str(result_path), "network_contract": "trusted_external_unmanaged",
        "native_quality_applied": False, "native_recovery_supported": False, "records": 0,
    }
    try:
        with tempfile.TemporaryDirectory(prefix="scrapy-", dir=output) as temporary:
            work = Path(temporary)
            pending = work / "records.jsonl"
            command = [sys.executable, "-m", "scrapy", "runspider", str(spider_path), "-O", str(pending)]
            for key, value in source.get("arguments", {}).items():
                command.extend(["-a", f"{key}={value}"])
            summary.update(_run_owned(command, cwd=config.root, output=pending, logs=work,
                                      timeout=float(timeout), maximum_bytes=maximum, should_stop=should_stop))
            if summary["returncode"]:
                raise RuntimeError("Scrapy exited unsuccessfully")
            if not pending.is_file() or pending.is_symlink() or pending.stat().st_size > maximum:
                raise ValueError("Scrapy result missing or over budget")
            count = 0
            with pending.open(encoding="utf-8") as stream:
                for line in stream:
                    if line.strip():
                        if not isinstance(json.loads(line), dict):
                            raise ValueError("Scrapy JSONL records must be objects")
                        count += 1
            if count == 0 and source.get("allow_empty") is not True:
                raise ValueError("Scrapy produced no verified records")
            os.replace(pending, result_path)
            summary.update(status="succeeded", records=count)
    except Exception as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
        raise RuntimeError(f"Scrapy执行失败，详见 {output / 'scrapy_summary.json'}") from exc
    finally:
        (output / "scrapy_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
