"""依赖安装器单元测试（顺序多源回退 / 严格版本 / 超时归因 / 失败分类）。

所有用例都**不真的装包、不访问网络**：``_run_pip`` 与 ``_dry_run_download_bytes``
被 monkeypatch 成确定性替身。这样测试判据是确定性的（"看起来正常" ≠ "运行时正确"）。
"""

from __future__ import annotations

import subprocess

import pytest

from omnicrawler.services import dependency_installer as di


# ── 需求解析 / 版本校验 ──────────────────────────────────
class TestParseRequirement:
    def test_pins_and_ranges(self) -> None:
        assert di.parse_requirement("pdfplumber") == ("pdfplumber", "", "")
        assert di.parse_requirement("pdfplumber==0.11.4") == ("pdfplumber", "", "==0.11.4")
        assert di.parse_requirement("pdfplumber>=0.10,<0.12") == ("pdfplumber", "", ">=0.10,<0.12")
        assert di.parse_requirement("omnicrawler-platform[pdf]") == ("omnicrawler-platform", "pdf", "")

    def test_extras_with_spec(self) -> None:
        name, extras, spec = di.parse_requirement("omnicrawler-platform[browser]>=1.0")
        assert name == "omnicrawler-platform"
        assert extras == "browser"
        assert spec == ">=1.0"


class TestIsSatisfied:
    @pytest.mark.parametrize(
        ("version", "spec", "expected"),
        [
            ("0.11.4", "==0.11.4", True),
            ("0.11.5", "==0.11.4", False),
            ("0.11.4", ">=0.10", True),
            ("0.9.0", ">=0.10", False),
            ("0.11.4", ">=0.10,<0.12", True),
            ("0.12.0", ">=0.10,<0.12", False),
            ("0.12.0", ">=0.10,<=0.12", True),
            ("1.5", "!=1.5", False),
            ("1.6", "!=1.5", True),
            ("2.1.0", "~=2.1.0", True),
            ("2.2.0", "~=2.1.0", False),
            ("9.9.9", "", True),
        ],
    )
    def test_matrix(self, version: str, spec: str, expected: bool) -> None:
        assert di.is_satisfied(version, spec) is expected

    def test_unparseable_version_conservatively_passes(self) -> None:
        # 无法比较 ⇒ 保守放行（不因解析器局限把成功当失败）
        assert di.is_satisfied("weird-build", ">=1.0") is True


# ── 失败分类（决策五）─────────────────────────────────────
class TestClassifyPipError:
    def test_version_missing_is_not_network(self) -> None:
        output = "ERROR: Could not find a version that satisfies the requirement pdfplumber>=9.9\n" \
                 "ERROR: No matching distribution found for pdfplumber>=9.9"
        assert di.classify_pip_error(output) == di.PipFailureKind.VERSION

    def test_network_failure(self) -> None:
        assert di.classify_pip_error("Read timed out. (read timeout=15)") == di.PipFailureKind.NETWORK
        assert di.classify_pip_error("SSLError: certificate verify failed") == di.PipFailureKind.NETWORK
        assert di.classify_pip_error("Temporary failure in name resolution") == di.PipFailureKind.NETWORK

    def test_conflict(self) -> None:
        output = "ERROR: ResolutionImpossible: conflicting dependencies"
        assert di.classify_pip_error(output) == di.PipFailureKind.CONFLICT

    def test_unknown_defaults_to_network(self) -> None:
        assert di.classify_pip_error("something odd happened") == di.PipFailureKind.NETWORK


# ── 重包判定与超时 ────────────────────────────────────────
class TestTimeoutEstimation:
    def test_heavy_package_hits_table(self) -> None:
        heavy = di.resolve_heavy_package("paddleocr")
        assert heavy.is_heavy is True
        assert heavy.base_timeout == di.HEAVY_TIMEOUT_SECONDS

    def test_name_normalization(self) -> None:
        assert di.resolve_heavy_package("OpenCV_Python").is_heavy is True
        assert di.resolve_heavy_package("scikit_learn").is_heavy is True

    def test_override_wins(self) -> None:
        seconds = di.estimate_timeout_seconds("requests", "https://x/simple", override=42.0)
        assert seconds == 42.0

    def test_heavy_skips_dry_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        called = {"n": 0}

        def _fake_dry(*args, **kwargs):  # noqa: ANN002, ANN003
            called["n"] += 1
            return 10_000_000

        monkeypatch.setattr(di, "_dry_run_download_bytes", _fake_dry)
        seconds = di.estimate_timeout_seconds("paddleocr", "https://x/simple")
        assert seconds == di.HEAVY_TIMEOUT_SECONDS
        assert called["n"] == 0  # 命中重包表就不该再 dry-run

    def test_large_dry_run_scales_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(di, "_dry_run_download_bytes", lambda *a, **k: 100 * 1024 * 1024)
        seconds = di.estimate_timeout_seconds("requests", "https://x/simple")
        assert seconds > di.DEFAULT_TIMEOUT_SECONDS

    def test_dry_run_failure_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(di, "_dry_run_download_bytes", lambda *a, **k: None)
        seconds = di.estimate_timeout_seconds("requests", "https://x/simple")
        assert seconds == di.DEFAULT_TIMEOUT_SECONDS


# ── 顺序回退 / 健康分回写 ─────────────────────────────────
class _FakeRegistry:
    """记录 record_success/failure 调用的替身（断言"该摘的摘、不该摘的不摘"）。"""

    def __init__(self) -> None:
        self.successes: list[tuple[str, str]] = []
        self.failures: list[tuple[str, str]] = []

    def record_success(self, canonical: str, host: str) -> None:
        self.successes.append((canonical, host))

    def record_failure(self, canonical: str, host: str) -> None:
        self.failures.append((canonical, host))


def _patch_pip(monkeypatch: pytest.MonkeyPatch, script: dict[str, tuple[int, str]]) -> list[str]:
    """按 index_url 注入 pip 结果；返回被调用的 index_url 顺序。"""
    calls: list[str] = []

    def _fake_run(requirement, index_url, *, python_executable, timeout):  # noqa: ANN001
        calls.append(index_url)
        return script.get(index_url, (0, "Successfully installed"))

    monkeypatch.setattr(di, "_run_pip", _fake_run)
    monkeypatch.setattr(di, "estimate_timeout_seconds", lambda *a, **k: 30.0)
    return calls


class TestSequentialFailover:
    def test_official_first_then_switch_on_network_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sources = [("pypi.org", "pypi.org"), ("pypi.org", "mirrors.tuna.tsinghua.edu.cn")]
        calls = _patch_pip(monkeypatch, {
            di.OFFICIAL_PYPI_INDEX: (1, "Read timed out"),
            "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple": (0, "Successfully installed"),
        })
        monkeypatch.setattr(di, "_installed_version", lambda name: "1.2.3")
        registry = _FakeRegistry()
        result = di.install_dependency("requests>=1.0", sources=sources, registry=registry)

        assert result.ok is True
        assert calls[0] == di.OFFICIAL_PYPI_INDEX  # 官方源先试
        assert calls[1] == "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple"
        # 网络失败 ⇒ 官方被摘流
        assert ("pypi.org", "pypi.org") in registry.failures
        # 镜像成功 ⇒ 记成功
        assert ("pypi.org", "mirrors.tuna.tsinghua.edu.cn") in registry.successes

    def test_version_missing_does_not_trip_health(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """源可达但缺版本 ⇒ 换源但**不摘流**（决策五核心）。"""
        sources = [("pypi.org", "pypi.org"), ("pypi.org", "mirrors.aliyun.com")]
        _patch_pip(monkeypatch, {
            di.OFFICIAL_PYPI_INDEX: (1, "ERROR: No matching distribution found for x>=9.9"),
            "https://mirrors.aliyun.com/pypi/simple": (0, "Successfully installed"),
        })
        monkeypatch.setattr(di, "_installed_version", lambda name: "9.9.0")
        registry = _FakeRegistry()
        result = di.install_dependency("x>=9.9", sources=sources, registry=registry)

        assert result.ok is True
        assert registry.failures == []  # 关键：版本缺失没有摘流

    def test_all_sources_exhausted_reports_reasons(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sources = [("pypi.org", "pypi.org"), ("pypi.org", "mirrors.aliyun.com")]
        _patch_pip(monkeypatch, {
            di.OFFICIAL_PYPI_INDEX: (1, "Read timed out"),
            "https://mirrors.aliyun.com/pypi/simple": (1, "Connection refused"),
        })
        result = di.install_dependency("x", sources=sources)
        assert result.ok is False
        assert len(result.attempts) == 2
        assert "不可用" in result.detail or "失败" in result.summary

    def test_conflict_converges_early(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sources = [("pypi.org", "pypi.org"), ("pypi.org", "mirrors.aliyun.com")]
        calls = _patch_pip(monkeypatch, {
            di.OFFICIAL_PYPI_INDEX: (1, "ERROR: ResolutionImpossible"),
        })
        result = di.install_dependency("x", sources=sources)
        assert result.ok is False
        assert len(calls) == 1  # 冲突直接收敛，不再试第二个源
        assert "冲突" in result.detail

    def test_empty_sources_defaults_to_official(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _patch_pip(monkeypatch, {di.OFFICIAL_PYPI_INDEX: (0, "ok")})
        monkeypatch.setattr(di, "_installed_version", lambda name: "1.0")
        result = di.install_dependency("x", sources=[])
        assert result.ok is True
        assert calls == [di.OFFICIAL_PYPI_INDEX]


# ── 超时归因（决策九）─────────────────────────────────────
class TestTimeoutAttribution:
    def test_heavy_timeout_does_not_switch_source(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """大包超时 ⇒ 加时重试**同源**，不切源、不摘流。"""
        sources = [("pypi.org", "pypi.org"), ("pypi.org", "mirrors.aliyun.com")]
        calls: list[tuple[str, float]] = []
        state = {"n": 0}

        def _fake_run(requirement, index_url, *, python_executable, timeout):  # noqa: ANN001
            calls.append((index_url, timeout))
            state["n"] += 1
            if state["n"] == 1:
                raise subprocess.TimeoutExpired(cmd="pip", timeout=timeout)
            return 0, "Successfully installed"

        monkeypatch.setattr(di, "_run_pip", _fake_run)
        monkeypatch.setattr(di, "estimate_timeout_seconds", lambda *a, **k: 900.0)
        monkeypatch.setattr(di, "_installed_version", lambda name: "2.0")
        registry = _FakeRegistry()
        result = di.install_dependency("paddleocr", sources=sources, registry=registry)

        assert result.ok is True
        assert len(calls) == 2
        assert calls[0][0] == calls[1][0] == di.OFFICIAL_PYPI_INDEX  # 同源重试
        assert calls[1][1] > calls[0][1]  # 超时被放大
        assert registry.failures == []  # 加时成功 ⇒ 不摘流

    def test_heavy_timeout_twice_then_switch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sources = [("pypi.org", "pypi.org"), ("pypi.org", "mirrors.aliyun.com")]
        seen: list[str] = []

        def _fake_run(requirement, index_url, *, python_executable, timeout):  # noqa: ANN001
            seen.append(index_url)
            if index_url == di.OFFICIAL_PYPI_INDEX:
                raise subprocess.TimeoutExpired(cmd="pip", timeout=timeout)
            return 0, "Successfully installed"

        monkeypatch.setattr(di, "_run_pip", _fake_run)
        monkeypatch.setattr(di, "estimate_timeout_seconds", lambda *a, **k: 900.0)
        monkeypatch.setattr(di, "_installed_version", lambda name: "2.0")
        registry = _FakeRegistry()
        result = di.install_dependency("paddleocr", sources=sources, registry=registry)

        assert result.ok is True
        # 官方源两次（原+加时）后，才切到镜像
        assert seen == [di.OFFICIAL_PYPI_INDEX, di.OFFICIAL_PYPI_INDEX, "https://mirrors.aliyun.com/pypi/simple"]
        assert ("pypi.org", "pypi.org") in registry.failures  # 两次都超时 ⇒ 摘流


# ── 装完复检 ──────────────────────────────────────────────
class TestPostInstallVerification:
    def test_version_mismatch_fails_even_if_pip_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_pip(monkeypatch, {di.OFFICIAL_PYPI_INDEX: (0, "Successfully installed")})
        monkeypatch.setattr(di, "_installed_version", lambda name: "0.9.0")
        result = di.install_dependency("pdfplumber>=0.10", sources=[("pypi.org", "pypi.org")])
        assert result.ok is False
        assert "复检失败" in result.detail

    def test_version_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_pip(monkeypatch, {di.OFFICIAL_PYPI_INDEX: (0, "Successfully installed")})
        monkeypatch.setattr(di, "_installed_version", lambda name: "0.11.4")
        result = di.install_dependency("pdfplumber>=0.10,<0.12", sources=[("pypi.org", "pypi.org")])
        assert result.ok is True
        assert result.actual_version == "0.11.4"

    def test_success_summary_mentions_version(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_pip(monkeypatch, {di.OFFICIAL_PYPI_INDEX: (0, "ok")})
        monkeypatch.setattr(di, "_installed_version", lambda name: "3.1.4")
        result = di.install_dependency("requests", sources=[("pypi.org", "pypi.org")])
        assert "3.1.4" in result.summary
        assert "requests" in result.reason_chain()


# ── index_url 拼接 ────────────────────────────────────────
class TestIndexUrl:
    def test_official(self) -> None:
        assert di._index_url_for("pypi.org", "pypi.org") == di.OFFICIAL_PYPI_INDEX

    def test_known_mirror_uses_preset_url(self) -> None:
        assert di._index_url_for("mirrors.aliyun.com", "pypi.org") == "https://mirrors.aliyun.com/pypi/simple"

    def test_unknown_host_falls_back_to_simple(self) -> None:
        assert di._index_url_for("mirror.example.org", "pypi.org") == "https://mirror.example.org/simple"
