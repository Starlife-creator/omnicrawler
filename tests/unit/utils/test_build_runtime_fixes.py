"""Phase 5c 构建/运行时修复（F22/F27/F28/F39/F41/F54）回归测试。"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src"))


def test_f27_browsers_root_uses_runtime_dir_when_present(monkeypatch, tmp_path) -> None:
    """F27：browsers_root() 与 configure_runtime_environment 共用同一真源。"""
    from omnicrawler.core import runtime_paths

    app_dir = tmp_path / "app"
    runtime_browsers = app_dir / ".runtime" / "browsers"
    runtime_browsers.mkdir(parents=True)
    monkeypatch.setattr(runtime_paths, "is_frozen", lambda: False)
    monkeypatch.setattr(runtime_paths, "application_dir", lambda: app_dir)
    assert runtime_paths.browsers_root() == runtime_browsers


def test_f28_frozen_runtime_status_written(monkeypatch, tmp_path) -> None:
    """F28：冻结模式缺失资产写入 runtime-status.json 供 GUI 展示。"""
    from omnicrawler.core import runtime_paths

    app_dir = tmp_path / "app"
    app_dir.mkdir()
    original_root = runtime_paths.portable_data_root
    original_root.cache_clear()
    monkeypatch.setattr(runtime_paths, "is_frozen", lambda: True)
    monkeypatch.setattr(runtime_paths, "application_dir", lambda: app_dir)
    monkeypatch.setattr(runtime_paths, "portable_data_root", lambda: app_dir)

    runtime_paths.configure_runtime_environment()
    status_path = app_dir / ".omnicrawler" / "runtime-status.json"
    assert status_path.is_file()
    status = json.loads(status_path.read_text(encoding="utf-8"))
    # 无任何内置资产 → 全部 missing
    assert status["tesseract"] == "missing"
    assert status["chromium"] == "missing"
    monkeypatch.undo()
    original_root.cache_clear()


def test_f39_tesseract_ready_requires_language_packs(monkeypatch, tmp_path) -> None:
    """F39：Tesseract 就绪需 eng+chi_sim 语言包存在，且报告可用语言。"""
    import importlib


    tess = tmp_path / "tess"
    tessdata = tess / "tessdata"
    tessdata.mkdir(parents=True)
    (tess / "tesseract.exe").write_bytes(b"MZ")
    (tessdata / "eng.traineddata").write_bytes(b"x")
    (tessdata / "chi_sim.traineddata").write_bytes(b"x")

    monkeypatch.setenv("TESSERACT_CMD", str(tess / "tesseract.exe"))
    monkeypatch.setenv("TESSDATA_PREFIX", str(tessdata))
    for env_key in ("OMNICRAWL_SELENIUM_DRIVER", "OMNICRAWL_CHROME_BINARY", "PADDLE_PDX_CACHE_HOME"):
        monkeypatch.delenv(env_key, raising=False)

    module = importlib.import_module("omnicrawler.core.capabilities")
    report = module.capability_report(portable_paths=False)
    tesseract = report["native"]["tesseract"]
    assert tesseract["ready"] is True
    assert "chi_sim" in tesseract["languages"]

    # 删掉 chi_sim → 不再就绪且报告缺失
    (tessdata / "chi_sim.traineddata").unlink()
    report2 = module.capability_report(portable_paths=False)
    tesseract2 = report2["native"]["tesseract"]
    assert tesseract2["ready"] is False
    assert "chi_sim" in tesseract2["missing_languages"]


def test_f41_paddle_ready_requires_inference_pdiparams(monkeypatch, tmp_path) -> None:
    """F41：模型目录存在但权重不全不算就绪。"""
    import importlib


    models = tmp_path / "paddlex" / "official_models"
    (models / "PP-OCRv5_server_rec").mkdir(parents=True)
    (models / "PP-OCRv5_server_rec" / "inference.pdiparams").write_bytes(b"w")

    monkeypatch.setenv("PADDLE_PDX_CACHE_HOME", str(tmp_path / "paddlex"))
    for env_key in ("TESSERACT_CMD", "TESSDATA_PREFIX", "OMNICRAWL_SELENIUM_DRIVER", "OMNICRAWL_CHROME_BINARY"):
        monkeypatch.delenv(env_key, raising=False)

    module = importlib.import_module("omnicrawler.core.capabilities")
    report = module.capability_report(portable_paths=False)
    assert "PP-OCRv5_server_rec" in report["native"]["paddle_models"]["models"]

    # 权重缺失的模型不计数
    (models / "PP-OCRv5_mobile_det").mkdir()
    report2 = module.capability_report(portable_paths=False)
    assert report2["native"]["paddle_models"]["models"] == ["PP-OCRv5_server_rec"]


def test_f54_resolve_cli_candidates_lists_searched_paths(monkeypatch) -> None:
    """F54：resolve_cli_candidates 返回已尝试的候选路径，供失败消息展示。"""
    from omnicrawler.core import runtime_paths

    monkeypatch.setattr(runtime_paths, "is_frozen", lambda: False)
    monkeypatch.setattr(runtime_paths, "bundled_cli_path", lambda: None)
    monkeypatch.setattr(runtime_paths, "shutil", type("S", (), {"which": staticmethod(lambda n: None)}))
    # 屏蔽 .venv 入口探测，保证"无任何候选"分支可稳定回归
    monkeypatch.setattr(runtime_paths.Path, "is_file", lambda self: False)
    resolved, candidates = runtime_paths.resolve_cli_candidates("omnicrawler")
    assert resolved == "omnicrawler"
    assert "omnicrawler" in candidates


def test_f22_create_zip_source_date_epoch(tmp_path) -> None:
    """F22：SOURCE_DATE_EPOCH 控制归档条目时间戳（可复现构建）。"""
    import datetime
    import importlib.util
    import zipfile

    spec = importlib.util.spec_from_file_location(
        "create_zip", os.path.join(os.path.dirname(__file__), "..", "..", "..", "tools", "create_zip.py")
    )
    create_zip = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(create_zip)  # type: ignore[union-attr]

    project = tmp_path / "src"
    project.mkdir()
    (project / "a.txt").write_text("x", encoding="utf-8")
    archive_path = tmp_path / "out.zip"
    os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
    try:
        create_zip.create_zip(project, archive_path, "Demo", clean_source=False)
    finally:
        os.environ.pop("SOURCE_DATE_EPOCH", None)
    with zipfile.ZipFile(archive_path) as archive:
        info = archive.getinfo("Demo/a.txt")
    from datetime import timezone
    _utc = getattr(datetime, 'UTC', timezone.utc)
    expected = datetime.datetime.fromtimestamp(1700000000, tz=_utc).timetuple()[:6]
    assert info.date_time == expected
