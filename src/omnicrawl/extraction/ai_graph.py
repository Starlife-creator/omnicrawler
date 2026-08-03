"""AI 智能提取 pipeline — 分块 HTML → LLM → 结构化字段输出。

支持：
- 自动分块策略（auto/heading/fixed_chunk）
- 多 Provider 支持（OpenAI 兼容接口）
- 字段定义驱动提取
- 结果合并与置信度评分

用法:
    from omnicrawl.extraction.ai_graph import AIGraphExtractor
    extractor = AIGraphExtractor(provider=AIGraphExtractor.Provider(
        base_url="https://api.openai.com/v1",
        api_key="sk-...",
        model="gpt-4o",
    ))
    result = await extractor.extract(
        html="<html>...</html>",
        fields=[AIGraphExtractor.FieldDef(name="title", description="文章标题")],
    )
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

LOGGER = logging.getLogger(__name__)


class SplitStrategy(StrEnum):
    """HTML 分块策略。"""

    AUTO = "auto"          # 自动检测最佳分块方式
    HEADING = "heading"    # 按标题（h1-h6）分块
    FIXED_CHUNK = "fixed_chunk"  # 按固定字数分块


@dataclass
class FieldDef:
    """AI 提取的目标字段定义。"""

    name: str
    description: str = ""       # 字段的自然语言描述
    example: str = ""           # 示例值（帮助 LLM 理解）
    required: bool = False
    field_type: str = "text"    # text | number | date | url | list


@dataclass
class Provider:
    """AI Provider 配置。"""

    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o"
    timeout_seconds: int = 60
    max_tokens: int = 4096


class AIGraphExtractor:
    """AI 驱动的字段提取 pipeline。

    将 HTML 分块后发送给 LLM，LLM 返回结构化的字段值。
    适用于无法用传统选择器（CSS/XPath）精确提取的复杂页面。
    """

    # 默认 prompt 模板
    DEFAULT_PROMPT = (
        "你是一个网页数据提取助手。请从以下 HTML 片段中提取指定字段的值。\n\n"
        "## 目标字段\n"
        "{fields_spec}\n\n"
        "## HTML 内容\n"
        "```html\n"
        "{html_chunk}\n"
        "```\n\n"
        "## 输出格式\n"
        "请只返回 JSON，不要有任何其他文字。格式如下：\n"
        '{{"fields": {{"field_name": "extracted_value", ...}}, "confidence": 0.0-1.0}}\n'
    )

    def __init__(
        self,
        provider: Provider | None = None,
        prompt_template: str | None = None,
        chunk_size: int = 4000,
    ) -> None:
        self._provider = provider or Provider()
        self._prompt_template = prompt_template or self.DEFAULT_PROMPT
        self._chunk_size = max(500, min(chunk_size, 32000))

    # ── 公共 API ─────────────────────────────────────────────────────

    async def extract(
        self,
        html: str,
        fields: list[FieldDef],
        strategy: SplitStrategy = SplitStrategy.AUTO,
        max_tokens_per_chunk: int = 4000,
    ) -> dict[str, Any]:
        """对 HTML 分块 → LLM 提取 → 合并结果。

        Args:
            html: 目标页面 HTML
            fields: 要提取的字段定义列表
            strategy: 分块策略
            max_tokens_per_chunk: 每次 LLM 调用的最大 token 数

        Returns:
            {"fields": {name: value, ...}, "confidence": float, "chunks_processed": int}
        """
        chunks = self._split_html(html, strategy)
        if not chunks:
            chunks = [html]

        all_results: list[dict] = []
        for chunk in chunks:
            try:
                result = await self._extract_chunk(chunk, fields, max_tokens_per_chunk)
                all_results.append(result)
            except Exception as exc:
                LOGGER.warning("AI 提取分块失败: %s", exc)
                continue

        return self._merge_results(all_results, len(chunks))

    async def extract_single_page(
        self, html: str, fields: list[FieldDef]
    ) -> dict[str, Any]:
        """一站式：单次调用提取，不做分块。"""
        return await self._extract_chunk(html, fields, self._provider.max_tokens)

    # ── 内部分块 ──────────────────────────────────────────────────────

    def _split_html(self, html: str, strategy: SplitStrategy) -> list[str]:
        """根据策略将 HTML 分块。"""
        if strategy == SplitStrategy.FIXED_CHUNK:
            return self._fixed_chunk_split(html)

        if strategy == SplitStrategy.HEADING:
            chunks = self._heading_split(html)
            if chunks:
                return chunks

        # auto 或 heading 失败时回退到固定分块
        return self._fixed_chunk_split(html)

    def _fixed_chunk_split(self, html: str) -> list[str]:
        """按字符数平分 HTML。"""
        if len(html) <= self._chunk_size:
            return [html]
        chunks = []
        for i in range(0, len(html), self._chunk_size):
            chunk = html[i:i + self._chunk_size]
            if chunk.strip():
                chunks.append(chunk)
        return chunks

    def _heading_split(self, html: str) -> list[str]:
        """按 h1-h6 标签分块。"""
        import re
        # 找到所有标题位置
        pattern = re.compile(r'<(h[1-6])[^>]*>(.*?)</\1>', re.IGNORECASE | re.DOTALL)
        matches = list(pattern.finditer(html))
        if not matches:
            return []

        chunks = []
        for i, match in enumerate(matches):
            start = match.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(html)
            chunk = html[start:end]
            if chunk.strip():
                chunks.append(chunk)
        return chunks

    # ── LLM 调用 ──────────────────────────────────────────────────────

    async def _extract_chunk(
        self, html: str, fields: list[FieldDef], max_tokens: int
    ) -> dict[str, Any]:
        """调用 LLM 提取单个分块。"""
        import aiohttp

        fields_spec = self._build_fields_spec(fields)
        prompt = self._prompt_template.format(
            fields_spec=fields_spec,
            html_chunk=html[:self._chunk_size],
        )

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._provider.api_key}",
        }
        payload = {
            "model": self._provider.model,
            "messages": [
                {"role": "system", "content": "你是一个精确的网页数据提取器。只返回 JSON。"},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.1,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self._provider.base_url.rstrip('/')}/chat/completions",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self._provider.timeout_seconds),
            ) as resp:
                data = await resp.json()

        if "error" in data:
            raise RuntimeError(f"AI API 错误: {data['error']}")

        content = data.get("choices", [{}])[0].get("message", {}).get("content", "{}")
        return self._parse_response(content)

    def _build_fields_spec(self, fields: list[FieldDef]) -> str:
        """构建字段描述。"""
        lines = []
        for f in fields:
            line = f"- **{f.name}** ({f.field_type})"
            if f.description:
                line += f": {f.description}"
            if f.example:
                line += f" (如: {f.example})"
            if f.required:
                line += " [必填]"
            lines.append(line)
        return "\n".join(lines)

    def _parse_response(self, content: str) -> dict[str, Any]:
        """解析 LLM JSON 响应。"""
        # 尝试提取 JSON（可能有 markdown 包裹）
        content = content.strip()
        if content.startswith("```"):
            # 移除 markdown 代码块标记
            lines = content.split("\n")
            content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # 尝试提取 {} 包裹的 JSON
            import re
            match = re.search(r'\{[\s\S]*\}', content)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
            LOGGER.warning("无法解析 AI 响应为 JSON: %.200s", content)
            return {"fields": {}, "confidence": 0.0}

    def _merge_results(
        self, results: list[dict], total_chunks: int
    ) -> dict[str, Any]:
        """合并多个分块的提取结果。"""
        merged_fields: dict[str, Any] = {}
        confidences: list[float] = []

        for r in results:
            fields = r.get("fields", {})
            if isinstance(fields, dict):
                for name, value in fields.items():
                    # 保留第一个非空值
                    if name not in merged_fields or not merged_fields[name]:
                        merged_fields[name] = value
            conf = r.get("confidence", 0.0)
            if isinstance(conf, (int, float)):
                confidences.append(float(conf))

        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        return {
            "fields": merged_fields,
            "confidence": round(avg_confidence, 3),
            "chunks_processed": len(results),
            "total_chunks": total_chunks,
        }


# Backward-compatible aliases on the class (defined outside so mypy
# resolves the module-level Provider / FieldDef types correctly).
AIGraphExtractor.Provider = Provider  # type: ignore[attr-defined]
AIGraphExtractor.FieldDef = FieldDef  # type: ignore[attr-defined]
