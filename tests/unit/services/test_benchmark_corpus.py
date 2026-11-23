"""`services/benchmark_corpus.py` 的守卫：站点胶囊的落盘与**凭据泄漏检查**。

## 为什么这个模块值得测（此前 0% 覆盖）

`validate_capsule()` 有一条安全判据：

```python
if any("password" in ... or "token" in ... for item in value.get("cookie_names", [])):
    errors.append("Cookie清单疑似包含凭据而非名称")
```

它的意图是**拦住把 Cookie 的「值」当成「名字」写进基准语料** —— 那等于把
真实凭据提交进仓库。这条判据的存在本身就是证据：曾经有过（或极易发生）
这种泄漏。

测试它的价值不在于覆盖率，而在于**固定这条安全底线**：将来谁重构
`validate_capsule` 时，不能顺手把这条检查删掉。

## 边界

不验真实站点抓取。只验「落盘结构正确」与「凭据判据有效」。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from omnicrawler.services.benchmark_corpus import (
    SiteCapsule,
    materialize_capsule,
    minimum_catalog,
    validate_capsule,
)


def _capsule(**overrides) -> SiteCapsule:
    base = dict(
        capsule_id="demo-site",
        category="web",
        snapshot="<html><body>hi</body></html>",
        dom="<html><body>hi</body></html>",
        resources=(),
        har={},
        actions=(),
        cookie_names=("session_id", "locale"),
        expected={"title": "hi"},
        quality={"completeness": 0.9},
    )
    base.update(overrides)
    return SiteCapsule(**base)


def test_minimum_catalog_covers_all_required_branches() -> None:
    """基准目录必须覆盖五个维度（少一个就等于那个维度没有基准）。"""
    catalog = minimum_catalog()
    assert set(catalog) == {"web", "interactions", "api", "pdf", "security"}
    for key, values in catalog.items():
        assert values, f"维度 {key} 是空的 —— 该维度没有基准用例"


def test_materialize_writes_three_files(tmp_path: Path) -> None:
    """落盘必须产出 page.html / dom.html / capsule.json 三件套。"""
    target = materialize_capsule(tmp_path, _capsule())
    assert target.is_dir()
    for name in ("page.html", "dom.html", "capsule.json"):
        assert (target / name).is_file(), f"缺少 {name}"


def test_materialized_capsule_validates_clean(tmp_path: Path) -> None:
    """正常胶囊必须零错误。"""
    target = materialize_capsule(tmp_path, _capsule())
    assert validate_capsule(target) == ()


def test_missing_files_are_reported(tmp_path: Path) -> None:
    """缺文件必须被逐条报出（不能只报一个笼统错误）。"""
    target = materialize_capsule(tmp_path, _capsule())
    (target / "dom.html").unlink()
    errors = validate_capsule(target)
    assert any("dom.html" in err for err in errors), f"没报出缺 dom.html：{errors}"


def test_cookie_names_must_not_contain_credentials(tmp_path: Path) -> None:
    """**安全底线**：Cookie 清单里出现 password/token 这类名字 ⇒ 判红。

    这条判据防的是「把 Cookie 的值当成名字写进基准语料」——
    那等于把真实凭据提交进仓库。
    """
    for leaked in ("auth_token", "user_password", "PASSWORD"):
        target = materialize_capsule(
            tmp_path, _capsule(capsule_id=f"leak-{leaked}", cookie_names=(leaked,))
        )
        errors = validate_capsule(target)
        assert any("凭据" in err for err in errors), (
            f"Cookie 名 {leaked!r} 未被判为凭据泄漏：{errors}"
        )


def test_broken_json_is_reported_not_raised(tmp_path: Path) -> None:
    """`capsule.json` 损坏时必须**返回错误**而不是抛异常。

    校验函数用于批量扫描语料：一条坏数据不该让整个扫描崩掉。
    """
    target = materialize_capsule(tmp_path, _capsule())
    (target / "capsule.json").write_text("{not json", encoding="utf-8")
    errors = validate_capsule(target)
    assert any("无法解析" in err for err in errors), f"没报出解析失败：{errors}"


def test_capsule_serializes_expected_and_quality(tmp_path: Path) -> None:
    """`expected` 与 `quality` 必须原样落进 capsule.json（下游据此判分）。"""
    target = materialize_capsule(
        tmp_path, _capsule(expected={"title": "x"}, quality={"completeness": 0.5})
    )
    data = json.loads((target / "capsule.json").read_text(encoding="utf-8"))
    assert data["expected"] == {"title": "x"}
    assert data["quality"]["completeness"] == 0.5


@pytest.mark.parametrize("bad_id", ["", "..", "a/b"])
def test_capsule_id_is_used_as_directory_name(tmp_path: Path, bad_id: str) -> None:
    """`capsule_id` 直接当目录名用 —— 记录其行为，便于将来加校验时对齐预期。

    当前实现不校验 id，所以 `a/b` 会真的建子目录、`..` 会落到父目录。
    本用例把现状钉住：若将来加校验，这里会红，提醒同步更新文档。
    """
    target = materialize_capsule(tmp_path, _capsule(capsule_id=bad_id))
    assert target.is_dir()
