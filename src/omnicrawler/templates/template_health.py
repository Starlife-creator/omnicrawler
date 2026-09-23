from __future__ import annotations

import hashlib
import json
import re
import zipfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from ..core.utils import validate_user_agent_honesty
from .template_catalog import TemplateCatalog, TemplateRecord


@dataclass(frozen=True, slots=True)
class TemplateHealth:
    template_id: str
    ok: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StructureSnapshot:
    template_id: str
    captured_at: str
    source_url: str
    content_sha256: str
    features: tuple[str, ...]
    field_success: dict[str, float]

    @classmethod
    def from_html(
        cls,
        template_id: str,
        source_url: str,
        html: str,
        field_success: dict[str, float] | None = None,
    ) -> StructureSnapshot:
        return cls(
            template_id=template_id,
            captured_at=datetime.now(UTC).isoformat(),
            source_url=source_url,
            content_sha256=hashlib.sha256(html.encode("utf-8")).hexdigest(),
            features=tuple(sorted(_html_features(html))),
            field_success=field_success or {},
        )

    def similarity(self, previous: StructureSnapshot) -> float:
        current = set(self.features)
        old = set(previous.features)
        if not current and not old:
            return 1.0
        return len(current & old) / max(1, len(current | old))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> StructureSnapshot:
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["features"] = tuple(raw.get("features", []))
        return cls(**raw)


def validate_template(record: TemplateRecord) -> TemplateHealth:
    errors: list[str] = []
    warnings: list[str] = []
    meta = record.metadata
    if not re.fullmatch(r"[a-z0-9][a-z0-9._/-]*", meta.template_id):
        errors.append("template.id must use stable lowercase path characters")
    if not meta.description.strip():
        errors.append("template.description is required")
    if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?(?:[-+][A-Za-z0-9.-]+)?", meta.version):
        errors.append("template.version must be a semantic version")
    if not meta.capabilities:
        warnings.append("template.capabilities is empty")
    if not meta.verified_at:
        warnings.append("template.verified_at is missing")
    placeholders = TemplateCatalog.placeholders(record)
    undeclared = placeholders - set(meta.placeholders)
    if undeclared:
        errors.append("undeclared placeholders: " + ", ".join(sorted(undeclared)))
    unused = set(meta.placeholders) - placeholders
    if unused:
        warnings.append("unused placeholder declarations: " + ", ".join(sorted(unused)))
    source = record.config.get("source", {})
    if not isinstance(source, dict) or not source.get("kind"):
        errors.append("source.kind is required")
    if not isinstance(source, dict) or not isinstance(source.get("seeds"), list) or not source.get("seeds"):
        errors.append("source.seeds must contain at least one entry")
    # B1：JSON 模式键契约。section 级 `item_selector` 已废弃（加载期迁移会掩盖它，
    # 但模板必须直接写对契约键）；字段契约是 `path`/`paths` ——
    # `extractors.json_field_values` 从不读 `selector`，写 selector = 确定性取空
    # （2026-09-23 审计：7 个 builtin JSON 模板因此塌缩成 1 条空记录）。
    # 注意只查 json 模式：HTML 模式的 item_selector/selector 是正确用法。
    extract_cfg = record.config.get("extract")
    if isinstance(extract_cfg, dict) and extract_cfg.get("mode") == "json":
        if extract_cfg.get("item_selector"):
            errors.append("extract.item_selector is deprecated in json mode: use item_path")
        for field_name, field_rule in (extract_cfg.get("fields") or {}).items():
            if (
                isinstance(field_rule, dict)
                and "selector" in field_rule
                and not ({"path", "paths"} & field_rule.keys())
            ):
                errors.append(
                    f"extract.fields.{field_name}: json field contract is path/paths, not selector"
                )
    # B2：UA 诚实性在**配置入口**的守卫。core/utils 的铁则此前只覆盖
    # build_user_agent 的函数出口；模板硬编码的 http.user_agent 会经
    # browser_pool 直接生效并绕过该守卫（2026-09-23 审计：5 个 social 模板
    # 硬编码 Chrome/127 UA）。命中浏览器伪造签名 ⇒ error。
    http_cfg = record.config.get("http")
    if isinstance(http_cfg, dict) and http_cfg.get("user_agent"):
        try:
            validate_user_agent_honesty(
                str(http_cfg["user_agent"]), profile_name=f"template:{meta.template_id}"
            )
        except ValueError as exc:
            errors.append(str(exc))
    # B11-006 / B05-009：模板不得翻转安全关键配置——`deep_merge` 会把模板段覆盖进
    # 用户配置，`validate_template` 是发布前最后一道闸。安全键只允许默认/更严方向。
    safety_violations = _unsafe_security_overrides(record.config)
    if safety_violations:
        errors.extend(safety_violations)
    return TemplateHealth(meta.template_id, not errors, tuple(errors), tuple(warnings))


def _unsafe_security_overrides(config: Any) -> list[str]:
    """检查模板配置是否把安全键翻转到宽松方向（fail-open）。

    安全默认方向（来自 DEFAULTS）：respect_robots=True, allow_private_network=False,
    verify_tls=True, egress.enabled=True, allow_unintercepted_selenium=False。
    模板不应覆盖为危险值（即使签过名，也应 fail-closed 拒绝）。
    """
    if not isinstance(config, dict):
        return []
    violations: list[str] = []

    def _check(mapping: dict[str, Any], prefix: str) -> None:
        if not isinstance(mapping, dict):
            return
        http = mapping.get("http")
        if isinstance(http, dict):
            if http.get("respect_robots") is False:
                violations.append(f"{prefix}http.respect_robots=false（模板不得关闭 robots 合规）")
            if http.get("allow_private_network") is True:
                violations.append(f"{prefix}http.allow_private_network=true（模板不得开启私网访问）")
            if http.get("verify_tls") is False:
                violations.append(f"{prefix}http.verify_tls=false（模板不得关闭 TLS 校验）")
        egress = mapping.get("egress")
        if isinstance(egress, dict) and egress.get("enabled") is False:
            violations.append(f"{prefix}egress.enabled=false（模板不得关闭出口审计）")
        if isinstance(http, dict) and http.get("allow_unintercepted_selenium") is True:
            violations.append(f"{prefix}http.allow_unintercepted_selenium=true（模板不得允许未拦截浏览器）")

    _check(config, "")
    for key, value in config.items():
        if isinstance(value, dict):
            _check(value, f"{key}.")
    return violations


def validate_catalog(catalog: TemplateCatalog, *, include_legacy: bool = False) -> list[TemplateHealth]:
    records = catalog.discover()
    results: list[TemplateHealth] = []
    # B1：catalog 现在对单个坏文件容错（跳过而非炸掉整个目录），
    # 但 fail-closed 语义不变——破损文件必须在这里合成失败条目，
    # 否则 `all(item.ok)` 会对一个残缺集合恒真（静默放行）。
    parse_errors = catalog.parse_errors
    if parse_errors:
        detail = "; ".join(f"{path}: {message}" for path, message in parse_errors)
        results.append(TemplateHealth(
            "<catalog>",
            False,
            (f"catalog 包含 {len(parse_errors)} 个解析失败的模板文件: {detail}",),
            (),
        ))
    if not include_legacy:
        records = [record for record in records if record.metadata.category != "legacy"]
    results.extend(validate_template(record) for record in records)
    return results


class TemplatePack:
    """Safe import/export of template bundles with manifest hashes and no implicit overwrite.

    ⚠ B11-005：包内仅有 sha256 **完整性**校验，**不含签名**——本机制防传输损坏，
    不构成信任边界；模板来源信任校验由签名链（sign_plugin）负责，勿混用。
    """

    MANIFEST = "omnicrawler-template-pack.json"

    @classmethod
    def export(cls, records: Iterable[TemplateRecord], target: Path) -> Path:
        selected = list(records)
        target.parent.mkdir(parents=True, exist_ok=True)
        manifest: dict[str, Any] = {"format": 1, "created_at": datetime.now(UTC).isoformat(), "files": {}}
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for record in selected:
                name = f"templates/{record.metadata.template_id}.yaml"
                payload = record.path.read_bytes()
                manifest["files"][name] = hashlib.sha256(payload).hexdigest()
                archive.writestr(name, payload)
            archive.writestr(cls.MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
        return target

    @classmethod
    def import_pack(cls, pack: Path, target_dir: Path, *, overwrite: bool = False) -> list[Path]:
        created: list[Path] = []
        target_dir = target_dir.resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(pack) as archive:
            manifest = json.loads(archive.read(cls.MANIFEST))
            files = manifest.get("files", {})
            if not isinstance(files, dict):
                raise ValueError("Invalid template pack manifest")
            prepared: list[tuple[Path, bytes]] = []
            for name, expected_hash in files.items():
                pure = PurePosixPath(name)
                if pure.is_absolute() or ".." in pure.parts or not str(pure).startswith("templates/"):
                    raise ValueError(f"Unsafe template pack path: {name}")
                payload = archive.read(name)
                if hashlib.sha256(payload).hexdigest() != expected_hash:
                    raise ValueError(f"Template pack checksum mismatch: {name}")
                yaml.safe_load(payload.decode("utf-8"))
                destination = (target_dir / Path(*pure.parts[1:])).resolve()
                if target_dir not in destination.parents:
                    raise ValueError(f"Unsafe template destination: {name}")
                if destination.exists() and not overwrite:
                    raise FileExistsError(f"Template already exists: {destination}")
                prepared.append((destination, payload))
            for destination, payload in prepared:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(payload)
                created.append(destination)
        return created


def _html_features(html: str) -> set[str]:
    features: set[str] = set()
    for tag in re.findall(r"<\s*([A-Za-z][A-Za-z0-9:-]*)", html):
        features.add("tag:" + tag.casefold())
    for attribute, prefix in (("id", "id"), ("class", "class")):
        pattern = rf"\b{attribute}\s*=\s*['\"]([^'\"]+)['\"]"
        for value in re.findall(pattern, html, re.IGNORECASE):
            for token in value.split()[:20]:
                if len(token) <= 100:
                    features.add(f"{prefix}:{token.casefold()}")
    return features
