"""convertx 的「格式不支持」必须能自诊断（W1.2，2026-09-15）。

## 背景（CI 实证）

可选后端（pyarrow / duckdb）由 `_register_*()` 注册，**缺依赖时静默跳过注册**。
于是 `.parquet` 会退化成 `sniff_format() → None`，错误信息变成：

```
KeyError: ConvertX: 不支持的目标格式 None（已注册: ['.csv', '.db', ...]）
```

——**完全看不出缺什么**（CI 上缺 pyarrow 时就是这样，3 条 convertx 用例失败）。
资产判据要求「缺依赖给明确降级 + 补齐命令」，所以这里把它补上，并加机器检查：
① 真缺依赖时必须给出模块名与 `pip install 'omnicrawler[storage]'`；
② **装了却没注册**（别的原因）时**不得**乱指路。
"""

from __future__ import annotations

import importlib.util

import pytest

from omnicrawler.convertx._core import WRITERS, convert


@pytest.fixture
def _parquet_unregistered(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 `.parquet` 从注册表里摘掉，模拟"缺 pyarrow ⇒ 没注册"的状态（用后自动还原）。"""
    monkeypatch.delitem(WRITERS, ".parquet", raising=False)


@pytest.fixture
def _pyarrow_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """让 `find_spec("pyarrow")` 返回 None，模拟模块不可导入。"""
    real = importlib.util.find_spec

    def fake(name: str, *args: object, **kwargs: object):
        return None if name == "pyarrow" else real(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake)


def test_missing_optional_dependency_is_named_in_the_error(
    tmp_path, _parquet_unregistered: None, _pyarrow_absent: None
) -> None:
    src = tmp_path / "in.jsonl"
    src.write_text('{"a": 1}\n', encoding="utf-8")
    dst = tmp_path / "out.parquet"

    with pytest.raises(KeyError) as excinfo:
        convert(src, dst, dst_format=".parquet")

    message = str(excinfo.value)
    assert "pyarrow" in message, message
    assert "omnicrawler[storage]" in message, message


def test_installed_but_unregistered_backend_gets_no_misleading_hint(
    tmp_path, _parquet_unregistered: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """装了 pyarrow 却没注册（别的原因）⇒ 不得提示"去装依赖"（那会把人带偏）。"""
    import pyarrow  # noqa: F401  （本机已装；缺失时整条用例不适用）

    src = tmp_path / "in.jsonl"
    src.write_text('{"a": 1}\n', encoding="utf-8")

    with pytest.raises(KeyError) as excinfo:
        convert(src, tmp_path / "out.parquet", dst_format=".parquet")

    assert "pip install" not in str(excinfo.value), str(excinfo.value)


def test_unknown_extension_gets_no_hint(tmp_path) -> None:
    from omnicrawler.convertx._core import _missing_backend_hint

    assert _missing_backend_hint(tmp_path / "x.nope") == ""


def test_parquet_writer_is_registered_when_the_backend_is_available() -> None:
    """本机装了 pyarrow ⇒ `.parquet` 必须在注册表里（防止注册被悄悄破坏）。"""
    if importlib.util.find_spec("pyarrow") is None:
        pytest.skip("本机没有 pyarrow（storage extra）")
    assert ".parquet" in WRITERS
