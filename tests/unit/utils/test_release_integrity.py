import base64
import hashlib
import json
import zipfile
from pathlib import Path

from tools.check_release_integrity import (
    check_portable_zip,
    check_project,
    check_source_zip,
    check_wheel,
    check_wheel_requirements_against_lock,
)


def test_release_integrity_has_no_broken_local_imports_or_entry_points():
    project_root = Path(__file__).resolve().parents[3]
    assert check_project(project_root) == []


def test_local_import_check_parses_each_module_once(monkeypatch):
    """性能守卫：每个被导入的模块只应**解析一次**。

    反例（修复前的写法）：`cache.setdefault(target, _defined_names(target))` ——
    `setdefault` 的默认参数每次都求值，于是缓存形同虚设，同一模块被反复读盘 +
    `ast.parse` + `ast.walk`（profiler 实测：`ast.parse` 被调 1776 次而源文件只有约 350 个）。

    反向断言：把 `get/if` 改回 `setdefault` ⇒ 本用例转红。
    """
    from tools import check_release_integrity as cri

    calls: list[Path] = []
    real = cri._defined_names

    def counting(path: Path) -> set[str]:
        calls.append(path)
        return real(path)

    monkeypatch.setattr(cri, "_defined_names", counting)

    project_root = Path(__file__).resolve().parents[3]
    assert cri.check_local_imports(project_root / "src") == []

    assert calls, "守卫失去意义：一次都没解析到模块"
    repeated = len(calls) - len(set(calls))
    assert repeated == 0, (
        f"同一模块被重复解析 {repeated} 次（{len(calls)} 次调用 / {len(set(calls))} 个模块）"
        " ⇒ 解析结果没有按模块缓存（setdefault 的默认参数会被每次都求值）"
    )


def _write_wheel(path: Path, files: dict[str, bytes]) -> None:
    record = []
    for name, content in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
        record.append(f"{name},sha256={digest},{len(content)}")
    record.append("demo-1.0.dist-info/RECORD,,")
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
        archive.writestr("demo-1.0.dist-info/RECORD", "\n".join(record))


def test_wheel_integrity_checks_record_imports_and_entry_points(tmp_path):
    wheel = tmp_path / "demo.whl"
    _write_wheel(wheel, {
        "omnicrawler/__init__.py": b"",
        "omnicrawler/quality/__init__.py": b"",
        "omnicrawler/quality/diagnostics.py": b"class DiagnosticRecorder:\n    pass\n",
        "omnicrawler/pipeline.py": b"from .quality.diagnostics import DiagnosticRecorder\ndef main():\n    pass\n",
        "demo-1.0.dist-info/entry_points.txt": b"[console_scripts]\ndemo=omnicrawler.pipeline:main\n",
    })
    assert check_wheel(wheel) == []


def test_wheel_integrity_detects_missing_imported_symbol(tmp_path):
    wheel = tmp_path / "broken.whl"
    _write_wheel(wheel, {
        "omnicrawler/__init__.py": b"",
        "omnicrawler/quality/__init__.py": b"",
        "omnicrawler/quality/diagnostics.py": b"",
        "omnicrawler/pipeline.py": b"from .quality.diagnostics import DiagnosticRecorder\n",
    })
    assert any("has no DiagnosticRecorder" in issue for issue in check_wheel(wheel))


def test_source_zip_checks_paths_generated_files_and_imported_symbols(tmp_path):
    source_zip = tmp_path / "source.zip"
    files = {
        "Demo/pyproject.toml": "[project]\nname='demo'\nversion='1.0'\n",
        "Demo/README.md": "demo",
        "Demo/LICENSE": "MIT",
        "Demo/tools/check_release_integrity.py": "",
        "Demo/src/omnicrawler/__init__.py": "",
        "Demo/src/omnicrawler/quality/__init__.py": "",
        "Demo/src/omnicrawler/quality/diagnostics.py": "class DiagnosticRecorder:\n    pass\n",
        "Demo/src/omnicrawler/pipeline.py": "from .quality.diagnostics import DiagnosticRecorder\n",
    }
    with zipfile.ZipFile(source_zip, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    assert check_source_zip(source_zip) == []

    broken_zip = tmp_path / "broken-source.zip"
    with zipfile.ZipFile(broken_zip, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
        archive.writestr("Demo/coverage.json", "{}")
        archive.writestr("../escape.txt", "unsafe")
    issues = check_source_zip(broken_zip)
    assert any("generated file" in issue for issue in issues)
    assert any("unsafe archive path" in issue for issue in issues)


def _write_portable(
    path: Path,
    edition: str,
    *,
    corrupt_hash: bool = False,
    unverified_models: bool = False,
    extra_files: dict[str, bytes] | None = None,
) -> None:
    files: dict[str, bytes] = {
        "OmniCrawler.exe": b"MZ-gui",
        "omnicrawler-cli.exe": b"MZ-cli",
        "omnicrawler-worker.exe": b"MZ-worker",
        "_internal/python312.dll": b"runtime",
        "PORTABLE.flag": b"",
        "EDITION.txt": f"OmniCrawler {edition} portable edition\n".encode(),
        "CAPABILITIES.json": b"{}",
        "SBOM.json": b"{}",
        "THIRD_PARTY_NOTICES.md": b"notices",
        # F23：启动器/本地说明/发布信息/文档现在是必需文件
        "OmniCrawler-Launcher.bat": b"@echo off\r\nstart OmniCrawler.exe\r\n",
        "PORTABLE_README.txt": b"OmniCrawler portable\n",
        "RELEASE-INFO.json": b"{}",
        "docs/README.md": b"# docs",
        "browsers/chromium-1234/chrome-win64/chrome.exe": b"MZ-chromium",
    }
    if edition == "Full":
        model = "PP-OCRv5_server_rec"
        files.update({
            "runtime/selenium/chromedriver.exe": b"MZ-driver",
            "runtime/tesseract/tesseract.exe": b"MZ-tesseract",
            "runtime/tesseract/tessdata/eng.traineddata": b"eng",
            "runtime/tesseract/tessdata/chi_sim.traineddata": b"chi",
            "runtime/tesseract/tessdata/osd.traineddata": b"osd",
            "runtime/models/paddlex/omnicrawler-model-manifest.json": json.dumps({
                "verified": not unverified_models,
                "models": [model],
            }).encode(),
            f"runtime/models/paddlex/official_models/{model}/inference.pdiparams": b"model",
        })
    files.update(extra_files or {})
    records = {}
    for name, content in files.items():
        digest = hashlib.sha256(content).hexdigest()
        if corrupt_hash and name == "omnicrawler-cli.exe":
            digest = "0" * 64
        records[name] = {"sha256": digest, "bytes": len(content)}
    files["RUNTIME-MANIFEST.json"] = json.dumps({
        "format": 1,
        "created_at": "2026-07-25T00:00:00+00:00",
        "files": records,
    }).encode()
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(f"OmniCrawler/{name}", content)


def test_portable_zip_accepts_standard_and_full_editions(tmp_path):
    for edition in ("Standard", "Full"):
        archive = tmp_path / f"OmniCrawler-2.1.0-Windows-Portable-{edition}.zip"
        _write_portable(archive, edition)
        assert check_portable_zip(archive, verify_payloads=True) == []


def test_portable_zip_detects_unsafe_duplicates_and_manifest_hashes(tmp_path):
    archive = tmp_path / "OmniCrawler-2.1.0-Windows-Portable-Standard.zip"
    _write_portable(archive, "Standard", corrupt_hash=True)
    with zipfile.ZipFile(archive, "a") as bundle:
        bundle.writestr("OmniCrawler/OMNICRAWLER.exe", b"MZ-duplicate")
        bundle.writestr("../escape.txt", b"unsafe")
    issues = check_portable_zip(archive, verify_payloads=True)
    assert any("duplicate portable archive path" in issue for issue in issues)
    assert any("unsafe portable archive path" in issue for issue in issues)
    assert any("runtime manifest hash mismatch: omnicrawler-cli.exe" in issue for issue in issues)


def test_portable_zip_requires_full_runtime_assets(tmp_path):
    archive = tmp_path / "OmniCrawler-2.1.0-Windows-Portable-Full.zip"
    _write_portable(archive, "Standard")
    issues = check_portable_zip(archive)
    assert any("Full portable archive missing runtime asset" in issue for issue in issues)
    assert any("declares Standard, expected Full" in issue for issue in issues)


def test_portable_zip_requires_verified_paddle_model_manifest(tmp_path):
    archive = tmp_path / "OmniCrawler-2.1.0-Windows-Portable-Full.zip"
    _write_portable(archive, "Full", unverified_models=True)
    assert any("not marked verified" in issue for issue in check_portable_zip(archive))


def test_standard_portable_rejects_full_only_python_packages(tmp_path):
    archive = tmp_path / "OmniCrawler-2.1.0-Windows-Portable-Standard.zip"
    _write_portable(
        archive,
        "Standard",
        extra_files={"_internal/paddleocr/__init__.py": b""},
    )
    issues = check_portable_zip(archive)
    assert any("Standard portable archive contains Full-only assets" in issue for issue in issues)


def test_standard_portable_allows_internal_modules_with_similar_names(tmp_path):
    archive = tmp_path / "OmniCrawler-2.1.0-Windows-Portable-Standard.zip"
    _write_portable(
        archive,
        "Standard",
        extra_files={"_internal/omnicrawler/services/duckdb_store.py": b""},
    )
    assert check_portable_zip(archive) == []

# ── 产物依赖 ↔ 锁：逐条对账（《优化方案》§5.5 严格口径）─────────────────────


def _metadata(requires: tuple[str, ...], *, name: str = "demo", version: str = "1.0") -> bytes:
    # 注意顺序：所有头字段之后才是空行 + 正文（空行放早了 `Requires-Dist` 会落进 body）。
    lines = ["Metadata-Version: 2.1", f"Name: {name}", f"Version: {version}"]
    lines.extend(f"Requires-Dist: {item}" for item in requires)
    return ("\n".join(lines) + "\n\n").encode("utf-8")


def _write_lock(
    path: Path,
    requires: tuple[tuple[str, str, str], ...],
    *,
    name: str = "demo",
    version: str = "1.0",
) -> None:
    """最小 `uv.lock`（**TOML**，与真实锁同格式）：只放对账需要读的字段。"""
    lines = ["version = 1", "", "[[package]]", f'name = "{name}"', f'version = "{version}"']
    if requires:
        lines += [
            "",
            "[package.metadata]",
            "requires-dist = [",
        ]
        lines += [
            f'    {{ name = "{item_name}", specifier = "{specifier}", marker = "{marker}" }},'
            for item_name, specifier, marker in requires
        ]
        lines.append("]")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _wheel(tmp_path: Path, requires: tuple[str, ...]) -> Path:
    wheel = tmp_path / "demo-1.0-py3-none-any.whl"
    _write_wheel(wheel, {"demo-1.0.dist-info/METADATA": _metadata(requires)})
    return wheel


def test_wheel_requirements_match_lock_despite_formatting(tmp_path) -> None:
    """顺序/引号不同不算不一致：产物 `PyYAML<7,>=6` 与锁 `>=6,<7` 等价。"""
    wheel = _wheel(tmp_path, ("PyYAML<7,>=6", 'lxml<7,>=5; extra == "html"'))
    lock = tmp_path / "uv.lock"
    _write_lock(lock, (("PyYAML", ">=6,<7", ""), ("lxml", ">=5,<7", "extra == 'html'")))

    assert check_wheel_requirements_against_lock(wheel, lock) == []


def test_requirement_with_bracket_extras_keeps_its_specifier(tmp_path) -> None:
    """`psycopg[binary]>=3.2,<4`：去掉方括号段后**必须保留版本约束**。

    （早先按 `split("[")` 处理会把约束一起丢掉，从而对真实产物误报 5 条。）
    """
    wheel = _wheel(tmp_path, ("psycopg[binary]>=3.2,<4",))
    lock = tmp_path / "uv.lock"
    _write_lock(lock, (("psycopg", ">=3.2,<4", ""),))

    assert check_wheel_requirements_against_lock(wheel, lock) == []


def test_python_version_marker_is_normalized(tmp_path) -> None:
    """uv 把 `python_version` 存成 `python_full_version`，两者必须视为等价。"""
    wheel = _wheel(tmp_path, ("tomli>=2,<3; python_version < '3.11'",))
    lock = tmp_path / "uv.lock"
    _write_lock(lock, (("tomli", ">=2,<3", "python_full_version < '3.11'"),))

    assert check_wheel_requirements_against_lock(wheel, lock) == []


def test_changed_specifier_is_reported(tmp_path) -> None:
    """约束被改动（两个方向）都要报出来 —— 这是本检查存在的意义。"""
    wheel = _wheel(tmp_path, ("requests>=2.28,<3",))
    lock = tmp_path / "uv.lock"
    _write_lock(lock, (("requests", ">=2.30,<3", ""),))

    issues = check_wheel_requirements_against_lock(wheel, lock)
    assert len(issues) == 2, issues
    assert any("产物声明了锁里没有的依赖" in issue for issue in issues)
    assert any("锁里有的依赖产物没声明" in issue for issue in issues)


def test_wheel_version_must_match_lock(tmp_path) -> None:
    wheel = _wheel(tmp_path, ())
    lock = tmp_path / "uv.lock"
    _write_lock(lock, (), version="0.9")

    issues = check_wheel_requirements_against_lock(wheel, lock)
    assert any("产物版本与锁不一致" in issue for issue in issues), issues


def test_missing_lock_is_reported_not_silently_skipped(tmp_path) -> None:
    """缺锁文件必须**明确报错**，不能静默放过（否则门禁形同虚设）。"""
    wheel = _wheel(tmp_path, ("requests>=2.28,<3",))

    issues = check_wheel_requirements_against_lock(wheel, tmp_path / "missing.lock")
    assert issues and "缺少锁文件" in issues[0], issues


def test_check_wheel_only_compares_when_lock_is_provided(tmp_path) -> None:
    """不传 `lock_path` 时行为与从前一致（向后兼容）。"""
    wheel = _wheel(tmp_path, ("requests>=2.28,<3",))
    assert check_wheel(wheel) == []

