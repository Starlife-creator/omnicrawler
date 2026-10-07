from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ..services.component_manager import ComponentManager
from ..services.component_runtime import leased_ocr_runtime
from .ocr_result import OCRRichResult


class ComponentOCRBackend:
    """Preview adapter for a signed, self-contained offline OCR executable."""

    def __init__(self, config: dict[str, Any], *, manager: ComponentManager | None = None) -> None:
        self.name = str(config["component"])
        self.engine = str(config.get("backend", "paddle")).lower()
        self.lang = str(config.get("lang", "ch"))
        self.timeout = float(config.get("component_timeout_seconds", 120))
        if not math.isfinite(self.timeout) or not 0 < self.timeout <= 600:
            raise ValueError("OCR组件超时须在 0 到 600 秒之间")
        self.manager = manager
        with leased_ocr_runtime(self.name, engine=self.engine, manager=manager) as runtime:
            self.version = runtime.version

    def recognize(self, png_bytes: bytes) -> tuple[str, float | None]:
        result = self.recognize_rich(png_bytes)
        return result.text, result.confidence

    def recognize_rich(self, png_bytes: bytes) -> OCRRichResult:
        if not png_bytes.startswith(b"\x89PNG\r\n\x1a\n") or len(png_bytes) > 20 * 1024**2:
            raise ValueError("OCR组件只接收大小受限的 PNG 输入")
        with leased_ocr_runtime(self.name, engine=self.engine, manager=self.manager) as runtime:
            if runtime.version != self.version:
                raise RuntimeError("OCR组件版本已改变，请重新开始任务")
            with tempfile.TemporaryDirectory(prefix="omnicrawler-ocr-") as temporary:
                work = Path(temporary)
                (work / "input.png").write_bytes(png_bytes)
                input_sha = hashlib.sha256(png_bytes).hexdigest()
                request = {"format": 1, "engine": self.engine, "lang": self.lang,
                           "input": "input.png", "input_sha256": input_sha}
                (work / "request.json").write_text(json.dumps(request), encoding="utf-8")
                # No inherited credentials, proxies, Python import paths or host
                # freezer DLL paths. The executable carries its own runtime.
                environment = {key: os.environ[key] for key in ("SystemRoot", "SYSTEMROOT", "WINDIR", "TEMP", "TMP") if key in os.environ}
                environment["OMNICRAWLER_COMPONENT_OFFLINE"] = "1"
                with (work / "stderr.log").open("wb") as errors:
                    result = subprocess.run(
                        [str(runtime.executable), "--request", str(work / "request.json"),
                         "--result", str(work / "result.json"), "--offline"],
                        cwd=work, env=environment, stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL, stderr=errors, timeout=self.timeout,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
                    )
                if result.returncode:
                    raise RuntimeError(f"OCR组件执行失败，退出码 {result.returncode}；请检查离线模型和运行库")
                output = work / "result.json"
                if not output.is_file() or output.is_symlink() or output.stat().st_size > 1024**2:
                    raise ValueError("OCR组件结果缺失或超过大小限制")
                response = json.loads(output.read_text(encoding="utf-8"))
                if not isinstance(response, dict) or response.get("format") != 1 or response.get("input_sha256") != input_sha:
                    raise ValueError("OCR组件结果与输入或协议不匹配")
                text = response.get("text")
                confidence = response.get("confidence")
                if not isinstance(text, str):
                    raise ValueError("OCR组件结果文本无效")
                if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1):
                    raise ValueError("OCR组件结果置信度无效")
                structure = response.get("structure", {})
                if not isinstance(structure, dict):
                    raise ValueError("OCR组件结构无效")
                values: dict[str, Any] = {}
                for key in ("words", "blocks", "tables"):
                    entries = structure.get(key, [])
                    if not isinstance(entries, list) or len(entries) > 100000 or any(not isinstance(entry, dict) for entry in entries):
                        raise ValueError("OCR组件结构条目无效")
                    values[key] = entries
                metadata = structure.get("metadata", {})
                if not isinstance(metadata, dict):
                    raise ValueError("OCR组件结构元数据无效")
                return OCRRichResult(text, confidence, **values, metadata={**metadata,
                                     "component": self.name, "component_version": self.version})
