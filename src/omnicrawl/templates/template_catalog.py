from __future__ import annotations

import copy
import fnmatch
import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

LOGGER = logging.getLogger(__name__)

PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
TOKEN_RE = re.compile(r"[\w.-]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class TemplateMetadata:
    """Searchable, self-contained metadata stored in a template's ``template`` block."""

    template_id: str
    name: str
    category: str
    description: str = ""
    version: str = "1.0.0"
    tags: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    intents: tuple[str, ...] = ()
    recommended_when: str = ""
    limitations: str = ""
    why: str = ""
    domains: tuple[str, ...] = ()
    url_patterns: tuple[str, ...] = ()
    header_contains: Mapping[str, str] = field(default_factory=dict)
    html_contains: tuple[str, ...] = ()
    json_keys: tuple[str, ...] = ()
    placeholders: Mapping[str, Any] = field(default_factory=dict)
    source_urls: tuple[str, ...] = ()
    license: str = "OmniCrawler-MIT"
    verified_at: str = ""
    min_core_version: str = "0.0.1"
    deprecated: bool = False


@dataclass(frozen=True, slots=True)
class TemplateRecord:
    metadata: TemplateMetadata
    path: Path
    config: Mapping[str, Any]
    builtin: bool = True


@dataclass(frozen=True, slots=True)
class TemplateProbe:
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    body: str = ""
    json_data: Any = None


@dataclass(frozen=True, slots=True)
class TemplateMatch:
    record: TemplateRecord
    score: int
    reasons: tuple[str, ...]


class TemplateParseError(ValueError):
    """模板 YAML 解析失败。

    fail-closed：破损模板必须显式暴露（谁引入谁修），**不得**静默剔除出
    校验集合——否则 ``templates validate`` 的 ``all(item.ok)`` 会对一个
    残缺集合恒真，CI 绿灯放行破损模板进仓（审查报告 S33）。
    """


class TemplateCatalog:
    """Recursive template discovery, search, rendering and evidence-based recommendation.

    User templates override built-ins by stable template ID.  Legacy flat templates without
    metadata remain available for backward compatibility with older flat-template configs.
    """

    def __init__(self, builtin_dir: Path, user_dirs: Iterable[Path] = ()) -> None:
        self.builtin_dir = Path(builtin_dir)
        self.user_dirs = tuple(Path(path) for path in user_dirs)
        self._records: dict[str, TemplateRecord] | None = None
        # B02-010：内置源真值索引（path.resolve() → record）。即使内置模板被用户/市场
        # 同 id 覆盖，`builtin:` 逃生仍从这份真值解析，而非被覆盖的合并 dict。
        self._builtin_by_path: dict[Path, TemplateRecord] | None = None

    def discover(self, refresh: bool = False) -> list[TemplateRecord]:
        if self._records is not None and not refresh:
            return self._sorted(self._records.values())

        records: dict[str, TemplateRecord] = {}
        builtin_by_path: dict[Path, TemplateRecord] = {}
        for root, builtin in [(self.builtin_dir, True), *((path, False) for path in self.user_dirs)]:
            if not root.is_dir():
                continue
            for path in sorted((*root.rglob("*.yaml"), *root.rglob("*.yml"))):
                record = self._read_record(root, path, builtin)
                if record is None:
                    continue
                template_id = record.metadata.template_id
                if builtin:
                    builtin_by_path[record.path.resolve()] = record
                existing = records.get(template_id)
                if existing is not None and existing.builtin != builtin:
                    # B02-010：信任等级不同的两个模板共享同一 id（内置 vs 用户/市场）。
                    # 保留「用户/市场可覆盖内置」的既有设计，但覆盖不再静默——记录来源告警。
                    LOGGER.warning(
                        "模板 id 冲突：%r 已被来源不同的模板覆盖（%s → %s）",
                        template_id, existing.path, record.path,
                    )
                records[template_id] = record
        self._records = records
        self._builtin_by_path = builtin_by_path
        return self._sorted(records.values())

    def get(self, template_id: str) -> TemplateRecord | None:
        records = self._records_by_id()
        if template_id in records:
            return records[template_id]
        # builtin: 前缀 = 内置模板相对路径引用（YAML 双格式约定，见 b2_domain_mappings_default.yaml）。
        # 解析为 builtin 目录下的文件路径，与元数据 id 等价可查。
        # B02-010：从内置源真值解析，而非遍历已被覆盖挤出的合并 dict。
        if template_id.startswith("builtin:"):
            rel = Path(template_id[len("builtin:") :])
            target = (self.builtin_dir / rel).resolve()
            if self._builtin_by_path is None:
                self._ensure_builtin_index()
            builtin = self._builtin_by_path or {}
            record = builtin.get(target)
            if record is not None:
                return record
            # 兼容：真值索引未命中时回退到逐记录比对（覆盖旧的按 path 匹配行为）
            for record in builtin.values():
                if record.path.resolve() == target:
                    return record
        matches = [r for r in records.values() if r.path.stem == template_id or r.metadata.name == template_id]
        return matches[0] if len(matches) == 1 else None

    def _ensure_builtin_index(self) -> None:
        """独立构建内置源真值索引（不依赖 discover 的合并结果）。"""
        builtin_by_path: dict[Path, TemplateRecord] = {}
        if self.builtin_dir.is_dir():
            for path in sorted((*self.builtin_dir.rglob("*.yaml"), *self.builtin_dir.rglob("*.yml"))):
                record = self._read_record(self.builtin_dir, path, True)
                if record is not None:
                    builtin_by_path[record.path.resolve()] = record
        self._builtin_by_path = builtin_by_path

    def search(
        self,
        query: str = "",
        *,
        category: str = "",
        tags: Iterable[str] = (),
        capabilities: Iterable[str] = (),
        include_deprecated: bool = False,
    ) -> list[TemplateRecord]:
        terms = {term.casefold() for term in TOKEN_RE.findall(query)}
        wanted_tags = {tag.casefold() for tag in tags}
        wanted_capabilities = {item.casefold() for item in capabilities}
        ranked: list[tuple[int, TemplateRecord]] = []
        for record in self.discover():
            meta = record.metadata
            if meta.deprecated and not include_deprecated:
                continue
            if category and not (meta.category == category or meta.category.startswith(category.rstrip("/") + "/")):
                continue
            actual_tags = {tag.casefold() for tag in meta.tags}
            actual_capabilities = {item.casefold() for item in meta.capabilities}
            if not wanted_tags.issubset(actual_tags) or not wanted_capabilities.issubset(actual_capabilities):
                continue
            haystack = " ".join(
                (meta.template_id, meta.name, meta.category, meta.description, *meta.tags, *meta.capabilities)
            ).casefold()
            if terms and not all(term in haystack for term in terms):
                continue
            score = sum(10 if term in meta.name.casefold() else 3 for term in terms)
            ranked.append((score, record))
        return [record for _score, record in sorted(ranked, key=lambda item: (-item[0], item[1].metadata.template_id))]

    def recommend(self, probe: TemplateProbe, limit: int = 5, *, intent: str = "") -> list[TemplateMatch]:
        parsed = urlparse(probe.url)
        hostname = (parsed.hostname or "").casefold()
        url = probe.url.casefold()
        body = probe.body.casefold()
        headers = {str(key).casefold(): str(value).casefold() for key, value in probe.headers.items()}
        json_keys = self._json_keys(probe.json_data)
        matches: list[TemplateMatch] = []

        for record in self.discover():
            meta = record.metadata
            if meta.deprecated:
                continue
            score = 0
            reasons: list[str] = []
            if intent and intent.casefold() in {item.casefold() for item in meta.intents}:
                score += 60
                reasons.append(f"intent:{intent}")
            for domain in meta.domains:
                pattern = domain.casefold().lstrip(".")
                if hostname == pattern or hostname.endswith("." + pattern) or fnmatch.fnmatch(hostname, pattern):
                    score += 100
                    reasons.append(f"domain:{domain}")
                    break
            for pattern in meta.url_patterns:
                regex_match = False
                try:
                    regex_match = re.search(pattern, probe.url, re.IGNORECASE) is not None
                except re.error:
                    pass
                if fnmatch.fnmatch(url, pattern.casefold()) or regex_match:
                    score += 35
                    reasons.append(f"url:{pattern}")
                    break
            for key, value in meta.header_contains.items():
                header_key = key.casefold()
                if header_key in headers and value.casefold() in headers[header_key]:
                    score += 25
                    reasons.append(f"header:{key}")
            hits = [needle for needle in meta.html_contains if needle.casefold() in body]
            if hits:
                score += min(50, 15 * len(hits))
                reasons.append("html:" + ",".join(hits[:3]))
            key_hits = {key.casefold() for key in meta.json_keys} & json_keys
            if key_hits:
                score += min(45, 15 * len(key_hits))
                reasons.append("json:" + ",".join(sorted(key_hits)[:3]))
            if score:
                matches.append(TemplateMatch(record, score, tuple(reasons)))

        matches.sort(key=lambda match: (-match.score, match.record.metadata.template_id))
        return matches[: max(0, limit)]

    def render(
        self,
        template: str | TemplateRecord,
        values: Mapping[str, Any],
        *,
        strict: bool = True,
        include_metadata: bool = False,
    ) -> dict[str, Any]:
        record = self.get(template) if isinstance(template, str) else template
        if record is None:
            raise KeyError(f"Unknown template: {template}")
        data = copy.deepcopy(dict(record.config))
        declared = record.metadata.placeholders
        merged_values = {
            key: value.get("default") if isinstance(value, Mapping) else value
            for key, value in declared.items()
        }
        merged_values.update(values)
        missing: set[str] = set()

        def replace(value: Any) -> Any:
            if isinstance(value, dict):
                return {key: replace(item) for key, item in value.items()}
            if isinstance(value, list):
                return [replace(item) for item in value]
            if not isinstance(value, str):
                return value
            full = PLACEHOLDER_RE.fullmatch(value)
            if full and full.group(1) in merged_values and merged_values[full.group(1)] is not None:
                return copy.deepcopy(merged_values[full.group(1)])

            def substitution(match: re.Match[str]) -> str:
                key = match.group(1)
                if key not in merged_values or merged_values[key] is None:
                    missing.add(key)
                    return match.group(0)
                return str(merged_values[key])

            rendered = PLACEHOLDER_RE.sub(substitution, value)
            # E14：缺失键已在 substitution 回调中逐个记录；不再对渲染结果二次 findall，
            # 避免"替换值本身含占位符"被误报为缺失
            return rendered

        rendered = replace(data)
        if not include_metadata:
            rendered.pop("template", None)
        if strict and missing:
            raise ValueError("Missing template values: " + ", ".join(sorted(missing)))
        return rendered

    @staticmethod
    def placeholders(record: TemplateRecord) -> set[str]:
        text = yaml.safe_dump(dict(record.config), allow_unicode=True, sort_keys=False)
        return set(PLACEHOLDER_RE.findall(text))

    def _records_by_id(self) -> dict[str, TemplateRecord]:
        if self._records is None:
            self.discover()
        return self._records or {}

    def _read_record(self, root: Path, path: Path, builtin: bool) -> TemplateRecord | None:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise TemplateParseError(f"模板 YAML 解析失败: {path}（{exc}）") from exc
        except (OSError, UnicodeError) as exc:
            raise TemplateParseError(f"模板文件读取/解码失败: {path}（{exc}）") from exc
        if not isinstance(raw, dict):
            raise TemplateParseError(f"模板 YAML 顶层必须是映射: {path}")
        relative = path.relative_to(root).with_suffix("")
        fallback_id = "/".join(relative.parts)
        block = raw.get("template", {})
        if not isinstance(block, dict):
            block = {}
        # Ignore auxiliary PDF field maps and other YAML resources that are not crawl templates.
        if "project" not in raw or "source" not in raw:
            return None
        metadata = TemplateMetadata(
            template_id=str(block.get("id") or fallback_id),
            name=str(block.get("name") or path.stem.replace("_", " ").title()),
            category=str(block.get("category") or (relative.parts[0] if len(relative.parts) > 1 else "legacy")),
            description=str(block.get("description") or raw.get("project", {}).get("name", "")),
            version=str(block.get("version") or "1.0.0"),
            tags=self._tuple(block.get("tags")),
            capabilities=self._tuple(block.get("capabilities")),
            intents=self._tuple(block.get("intents")),
            recommended_when=str(block.get("recommended_when") or ""),
            limitations=str(block.get("limitations") or block.get("not_for") or ""),
            why=str(block.get("why") or ""),
            domains=self._tuple(block.get("domains")),
            url_patterns=self._tuple(block.get("url_patterns")),
            header_contains=self._string_mapping(block.get("header_contains")),
            html_contains=self._tuple(block.get("html_contains")),
            json_keys=self._tuple(block.get("json_keys")),
            placeholders=block.get("placeholders", {}) if isinstance(block.get("placeholders", {}), dict) else {},
            source_urls=self._tuple(block.get("source_urls")),
            license=str(block.get("license") or "OmniCrawler-MIT"),
            verified_at=str(block.get("verified_at") or ""),
            min_core_version=str(block.get("min_core_version") or "0.7.0"),
            deprecated=bool(block.get("deprecated", False)),
        )
        return TemplateRecord(metadata, path.resolve(), raw, builtin)

    @staticmethod
    def _tuple(value: Any) -> tuple[str, ...]:
        if isinstance(value, str):
            return (value,)
        if isinstance(value, list):
            return tuple(str(item) for item in value)
        return ()

    @staticmethod
    def _string_mapping(value: Any) -> dict[str, str]:
        return {str(key): str(item) for key, item in value.items()} if isinstance(value, dict) else {}

    @staticmethod
    def _json_keys(value: Any) -> set[str]:
        if value is None:
            return set()
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (ValueError, TypeError):
                return set()
        keys: set[str] = set()

        def walk(item: Any) -> None:
            if isinstance(item, dict):
                for key, child in item.items():
                    keys.add(str(key).casefold())
                    walk(child)
            elif isinstance(item, list):
                for child in item[:50]:
                    walk(child)

        walk(value)
        return keys

    @staticmethod
    def _sorted(records: Iterable[TemplateRecord]) -> list[TemplateRecord]:
        return sorted(records, key=lambda record: (record.metadata.category, record.metadata.name, record.metadata.template_id))


def bundled_template_catalog(user_dirs: Iterable[Path] = ()) -> TemplateCatalog:
    return TemplateCatalog(Path(__file__).resolve().parent, user_dirs)
