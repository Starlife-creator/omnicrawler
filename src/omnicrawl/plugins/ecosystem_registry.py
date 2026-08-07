"""Signed ecosystem metadata, revocation and template quality scoring."""

from __future__ import annotations

from dataclasses import dataclass

from .signing import verify_bytes


@dataclass(frozen=True, slots=True)
class EcosystemPackage:
    package_id: str
    version: str
    publisher: str
    permissions: tuple[str, ...]
    dependencies: tuple[str, ...]
    license: str
    compatible_core: str
    signature_valid: bool
    automated_tests_passed: bool
    signature: bytes = b""
    signature_algorithm: str = ""


def _ecosystem_payload(package: EcosystemPackage) -> bytes:
    """Canonical bytes covered by an ecosystem package signature."""

    return "|".join([
        package.package_id, package.version, package.publisher,
        ",".join(package.permissions), package.license, package.compatible_core,
    ]).encode("utf-8")


class EcosystemRegistry:
    def __init__(self) -> None:
        self.revoked: dict[tuple[str, str], str] = {}
        self.disabled: set[str] = set()

    def revoke(self, package_id: str, version: str, advisory: str) -> None:
        if not advisory:
            raise ValueError("撤回必须关联安全公告或原因")
        self.revoked[(package_id, version)] = advisory
        self.disabled.add(package_id)

    def installable(self, package: EcosystemPackage) -> tuple[bool, str]:
        if (package.package_id, package.version) in self.revoked:
            return False, self.revoked[(package.package_id, package.version)]
        if not package.signature_valid or not package.automated_tests_passed:
            return False, "签名或自动测试未通过"
        if not package.license or not package.compatible_core:
            return False, "缺少许可或兼容信息"
        return True, "verified"

    def verify_package(self, package: EcosystemPackage, trust_source: str) -> tuple[bool, str]:
        """Verify ``package.signature`` against the configured trust root.

        Callers should set ``signature_valid`` from this result before calling
        :meth:`installable`, so the field reflects a real cryptographic check
        rather than an uninitialized default.
        """

        if not package.signature:
            return False, "包未签名"
        if verify_bytes(_ecosystem_payload(package), package.signature, trust_source):
            return True, "verified"
        return False, "签名校验失败"


def template_quality_score(*, recent_validation: float, success: float, completeness: float, reuse: float, drift_recovery: float) -> float:
    values = (recent_validation, success, completeness, reuse, drift_recovery)
    if any(not 0 <= value <= 1 for value in values):
        raise ValueError("模板质量指标必须在0到1之间")
    return round(recent_validation * 0.25 + success * 0.25 + completeness * 0.2 + reuse * 0.15 + drift_recovery * 0.15, 4)

