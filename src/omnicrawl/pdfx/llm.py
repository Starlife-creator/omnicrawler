from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..security.controlled_http import scoped_json_request
from .config import ProjectConfig
from .parser import render_page
from .retrieval import CandidatePage
from .utils import extract_json_object, retry

SYSTEM_PROMPT = """你是一个严格的文档数据抽取程序。只能依据用户给出的文件名和候选页面原文抽取，禁止根据常识、上下文猜测或补全。

必须遵守：
1. 输出必须是一个JSON对象，不要输出解释或Markdown。
2. 顶层格式为 {"document_type": string|null, "records": [...]}。
3. 每一笔独立事项对应records中的一项；不得把多笔事项合并。
4. 每项格式为 {"fields": {字段名: {"raw_value": string|null, "page_no": integer|null, "evidence": string|null}}}。
5. 找不到的字段必须为null，不能编造。
6. raw_value保留原文，不换算单位，不自行改写企业名称。
7. evidence必须是支持该值的简短原文；page_no必须对应输入中的页码。
8. 总额度、单笔金额、实际发生额、余额等不同口径不得混淆。
9. 如果文档不包含目标事项，records返回空数组。
"""


def _field_instructions(config: ProjectConfig) -> str:
    items: list[dict[str, Any]] = []
    for spec in config.fields:
        items.append({
            "name": spec.name,
            "label": spec.label,
            "description": spec.description,
            "type": spec.type,
            "aliases": spec.aliases,
            "required": spec.required,
        })
    return json.dumps(items, ensure_ascii=False, indent=2)


def build_user_content(
    config: ProjectConfig,
    filename: str,
    pages: list[CandidatePage],
    pdf_path: str | None = None,
) -> str | list[dict[str, Any]]:
    max_chars = int(config.extraction.get("max_chars_per_page", 12000))
    text_parts = [
        f"文件名：{filename}",
        "目标字段定义：",
        _field_instructions(config),
        "候选页面：",
    ]
    for page in pages:
        text = page.text[:max_chars]
        text_parts.append(f"\n===== 第{page.page_no}页 =====\n{text}")
    prompt = "\n".join(text_parts)

    if not config.llm.get("include_page_images", False) or not pdf_path:
        return prompt
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    dpi = int(config.llm.get("image_dpi", 144))
    for page in pages:
        png = render_page(pdf_path, page.page_no, dpi=dpi)
        encoded = base64.b64encode(png).decode("ascii")
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{encoded}", "detail": "high"},
        })
    return content


class OpenAICompatibleClient:
    def __init__(self, config: dict[str, Any], *, workspace: str | Path):
        self.api_key = str(config.get("api_key", ""))
        self.base_url = str(config.get("base_url", "https://api.openai.com/v1")).rstrip("/")
        self.model = str(config.get("model", ""))
        self.timeout = float(config.get("timeout_seconds", 180))
        self.max_tokens = int(config.get("max_output_tokens", 4000))
        self.temperature = float(config.get("temperature", 0))
        self.attempts = int(config.get("retry_attempts", 4))
        self.max_response_bytes = int(config.get("max_response_bytes", 10 * 1024 * 1024))
        self.workspace = Path(workspace).expanduser().resolve()
        if not self.api_key:
            raise ValueError("LLM API Key为空，请设置环境变量或关闭llm.provider")
        if not self.model:
            raise ValueError("llm.model为空")
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("llm.base_url 必须是有效的 HTTP(S) 地址")
        if not 1024 <= self.max_response_bytes <= 50 * 1024 * 1024:
            raise ValueError("llm.max_response_bytes 必须在1KB到50MB之间")
        if not 1 <= self.timeout <= 3600:
            raise ValueError("llm.timeout_seconds 必须在1到3600之间")
        if not 1 <= self.attempts <= 20:
            raise ValueError("llm.retry_attempts 必须在1到20之间")
        if not 1 <= self.max_tokens <= 1_000_000:
            raise ValueError("llm.max_output_tokens 必须在1到1000000之间")
        if not -2 <= self.temperature <= 2:
            raise ValueError("llm.temperature 必须在-2到2之间")

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def extract(self, user_content: str | list[dict[str, Any]]) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
        }

        def request_once() -> dict[str, Any]:
            body = scoped_json_request(
                self.endpoint,
                workspace=self.workspace,
                purpose="ai",
                method="POST",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                timeout_seconds=self.timeout,
                max_response_bytes=self.max_response_bytes,
        user_agent="OmniCrawler-PDF/2.7 LLM extraction",
            )
            choices = body.get("choices") or []
            if not choices:
                raise RuntimeError(f"LLM响应缺少choices: {str(body)[:1000]}")
            content = choices[0].get("message", {}).get("content", "")
            return extract_json_object(content)

        return retry(request_once, attempts=self.attempts, base_delay=2.0)


def create_llm_client(config: ProjectConfig) -> OpenAICompatibleClient | None:
    provider = str(config.llm.get("provider", "disabled")).lower()
    if provider == "disabled":
        return None
    if provider == "openai_compatible":
        return OpenAICompatibleClient(config.llm, workspace=config.work_dir)
    raise ValueError(f"不支持的LLM provider: {provider}")
