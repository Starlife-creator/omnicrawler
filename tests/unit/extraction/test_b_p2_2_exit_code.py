"""B-P2-2：AutoConfigUnverifiedError 与 ValueError 统一转 SystemExit(3)，
打印可读说明，不再以 Python traceback 退出。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import omnicrawler.extraction.intelligent_scraper as scraper_module
from omnicrawler.extraction.intelligent_scraper import AutoConfigUnverifiedError, main


def _write_html(tmp_path: Path) -> Path:
    path = tmp_path / "page.html"
    path.write_text(
        "<html><body><aside class='sidebar'><a href='/x'>菜单</a></aside></body></html>",
        encoding="utf-8",
    )
    return path


def _base_config() -> dict[str, Any]:
    return {
        "project": {"name": "b-p2-2", "workspace": "work"},
        "source": {"kind": "static_html"},
        "crawl": {"max_pages": 1},
        "extract": {
            "mode": "html",
            "item_selector": "aside.sidebar a",
            "fields": {"标题": {"selector": "a"}},
        },
        "outputs": {},
    }


def test_value_error_from_task_goal_exits_three(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    html_path = _write_html(tmp_path)
    monkeypatch.setattr(scraper_module, "analyze_to_config", lambda *a, **k: _base_config())
    monkeypatch.setattr("sys.argv", ["scraper", str(html_path)])

    settings = {
        "unsupported": ["浏览器点击"],
        "max_pages": 2,
        "fields": [],
        "multi_value_fields": [],
        "output_formats": ["xlsx"],
    }
    with pytest.raises(SystemExit) as exc:
        main(task_settings=settings)
    assert exc.value.code == 3
    err = capsys.readouterr().err
    assert "错误" in err


def test_unverified_error_from_check_exits_three(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    html_path = _write_html(tmp_path)
    monkeypatch.setattr(scraper_module, "analyze_to_config", lambda *a, **k: _base_config())
    monkeypatch.setattr("sys.argv", ["scraper", str(html_path)])

    settings = {
        "max_pages": 2,
        "fields": [],
        "multi_value_fields": [],
        "output_formats": ["xlsx"],
    }
    with pytest.raises(SystemExit) as exc:
        main(task_settings=settings)
    assert exc.value.code == 3
    err = capsys.readouterr().err
    # 容器落在 aside（页面框架）触发 AutoConfigUnverifiedError，说明文字可读
    assert "未识别出列表" in err


def test_final_verify_unverified_exits_three(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    html_path = _write_html(tmp_path)
    monkeypatch.setattr(scraper_module, "analyze_to_config", lambda *a, **k: _base_config())

    # _consider 内首调返回空报告；main 末尾最终验证调用再抛 AutoConfigUnverifiedError。
    calls = {"n": 0}

    def _verify(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] >= 2:
            raise AutoConfigUnverifiedError("最终验证：页面上采不到记录")
        return {"records": 0, "items": 0, "fields": {}}

    monkeypatch.setattr(scraper_module, "verify_config", _verify)
    monkeypatch.setattr("sys.argv", ["scraper", str(html_path)])

    with pytest.raises(SystemExit) as exc:
        main(task_settings=None)
    assert exc.value.code == 3
    err = capsys.readouterr().err
    assert "最终验证" in err
