from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from ..core.config import AppConfig


def run_scrapy(config: AppConfig) -> dict[str, Any]:
    source = config.section("source")
    spider = source.get("spider_file")
    if not spider:
        raise ValueError("Scrapy模式必须设置source.spider_file")
    spider_path = config.resolve(spider)
    if not spider_path.is_file():
        raise FileNotFoundError(f"Scrapy spider不存在: {spider_path}")
    output = config.workspace / "output"
    output.mkdir(parents=True, exist_ok=True)
    result_path = output / "scrapy_records.jsonl"
    command = [sys.executable, "-m", "scrapy", "runspider", str(spider_path), "-O", str(result_path)]
    for key, value in source.get("arguments", {}).items():
        command.extend(["-a", f"{key}={value}"])
    completed = subprocess.run(command, cwd=config.root, capture_output=True, text=True, check=False)
    summary = {
        "status": "succeeded" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode, "output": str(result_path),
        "stdout_tail": completed.stdout[-4000:], "stderr_tail": completed.stderr[-4000:],
    }
    (output / "scrapy_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if completed.returncode:
        raise RuntimeError(f"Scrapy执行失败，详见 {output / 'scrapy_summary.json'}")
    return summary
