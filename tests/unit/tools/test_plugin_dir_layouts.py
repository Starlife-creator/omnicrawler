"""插件目录发现：必须同时支持**开发态两层**与**市场安装三层**布局。

## 这条守卫防的是什么

两种真实布局：

```
开发态（手工放进目录）：
  <root>/plugins_installed/document-workbench/plugin.py

市场安装（package_importer.import_package_folder 的 final 路径）：
  <root>/plugins_installed/<creator_fingerprint>/document-workbench/<version>/plugin.py
```

`_plugin_dir()` 早先只查两层。后果：**从市场装完插件后 facade 报"未安装"**
——用户装好了却用不了。这正是"安装后功能一致"要防的失败。

修的时候还踩过一个坑：加了三层分支后又写了 `if not base.is_dir(): continue`，
而三层布局恰恰意味着两层路径不存在，于是分支永远进不去。
这里用一个"只有三层、没有两层"的 fixture 把它钉死。

## 判据

1. 三层布局可发现；
2. 两层布局仍可发现（不能为了三层破坏开发态）；
3. 多个版本并存时取**版本号最大**的；
4. 都没有时返回 `None`（不误报可用）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.services import document_facade

_FINGERPRINT = "4c3014804d85de2568f85e62a3048429"
_ENTRY = "x = 1\n"


@pytest.fixture(autouse=True)
def _clear_explicit_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """清掉显式覆盖，让目录探测按真实布局走。"""
    monkeypatch.delenv("OMNICRAWL_PDF_PLUGIN_DIR", raising=False)


def _make_market_layout(root: Path, version: str = "1.0.0") -> Path:
    """造市场安装的三层布局。"""
    directory = root / "plugins_installed" / _FINGERPRINT / "document-workbench" / version
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "plugin.py").write_text(_ENTRY, encoding="utf-8")
    return directory


def _make_dev_layout(root: Path) -> Path:
    """造开发态的两层布局。"""
    directory = root / "plugins_installed" / "document-workbench"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "plugin.py").write_text(_ENTRY, encoding="utf-8")
    return directory


def test_three_level_market_layout_is_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """市场安装的三层布局必须被发现（否则装完报"未安装"）。"""
    expected = _make_market_layout(tmp_path)
    monkeypatch.chdir(tmp_path)

    found = document_facade._plugin_dir()
    assert found is not None, (
        "三层布局未被发现——市场装完插件后 facade 会报\u201c未安装\u201d"
    )
    assert Path(found) == expected


def test_two_level_dev_layout_still_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """开发态两层布局仍要能发现（不能为了三层破坏原路径）。"""
    expected = _make_dev_layout(tmp_path)
    monkeypatch.chdir(tmp_path)

    found = document_facade._plugin_dir()
    assert found is not None, "开发态布局未被发现"
    assert Path(found) == expected


def test_highest_version_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """多版本并存时取版本号最大的那个。"""
    _make_market_layout(tmp_path, "1.0.0")
    newest = _make_market_layout(tmp_path, "1.2.0")
    _make_market_layout(tmp_path, "1.1.0")
    monkeypatch.chdir(tmp_path)

    found = document_facade._plugin_dir()
    assert found is not None
    assert Path(found) == newest, f"没取到最新版本：{found}"


def test_missing_plugin_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """都没有时返回 None——不得误报可用。"""
    (tmp_path / "plugins_installed").mkdir()
    monkeypatch.chdir(tmp_path)

    assert document_facade._plugin_dir() is None
    assert document_facade.document_capability()["available"] is False
