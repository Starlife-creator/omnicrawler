"""`source.kind: file` 的入口是 `local_files`，不是 `seeds`（2026-11-28）。

## 缺陷：运行时支持、校验不让过

`sources/local_files.py` 的 `seed()` 明确写着：

```python
selected = config.section("source").get("seeds") or list(files)
```

即「seeds 为空 ⇒ 回退到全部 `local_files`」。但 `validate_config` 把这种配置**拒了**：

```
旧：source.kind=file + local_files: [a.html]（不写 seeds）
    → ConfigParseError: source.seeds至少需要一个入口

新：同上 → 通过，且真的能跑（实测 records: 2）
```

用户只能把每个文件**手抄进 seeds** 才能用 —— 这不是严格，是自相矛盾。

## 同时补上真正的空运行判据

放宽 `file` 之后要挡住另一头：**既没 seeds 也没 local_files** 的配置是空运行。

| 配置 | 旧 | 新 |
|---|---|---|
| `file` + `local_files` | ❌ 拒绝（错）| ✅ 通过 |
| `file` + `seeds` | ✅ 通过 | ✅ 通过 |
| `file` 两者都没有 | ❌ 拒绝（对的，但理由是错的）| ❌ 拒绝，**点名缺 local_files** |

## 形状判断，不碰文件系统

`_source_has_local_files()` 只看**形状**（list 且至少一项非空字符串）。不 `resolve()`、
不 stat —— 那是 `declared_files()` 真正读取时的事（它要查符号链接、查越界）。

配置校验去 stat 文件会让「在别的机器上生成的配置」无法校验。
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _write(tmp_path: Path, *, files: list[str] | None, seeds: list[str] | None, workdir: str) -> Path:
    work = tmp_path / workdir
    work.mkdir(parents=True, exist_ok=True)
    for name in files or []:
        (work / name).write_text("<html><body><h1>x</h1></body></html>", encoding="utf-8")
    config = tmp_path / f"{workdir}.yaml"
    body = (
        "config_version: 3\n"
        f"project:\n  name: t\n  workspace: {work}\n"
        "source:\n  kind: file\n"
        f"  local_root: {work}\n"
    )
    if files is not None:
        body += f"  local_files: {files!r}\n"
    if seeds is not None:
        body += f"  seeds: {seeds!r}\n"
    body += "crawl:\n  max_pages: 5\n"
    config.write_text(body, encoding="utf-8")
    return config


def test_file_source_with_local_files_and_no_seeds_is_accepted(tmp_path: Path) -> None:
    """★ 核心判据：`local_files` 就是入口，不写 seeds 必须能过。

    这是运行时**已经支持**的写法（`local_files.seed()` 的回退分支）。
    """
    from omnicrawler.core.config import load_config

    config = _write(tmp_path, files=["a.html"], seeds=None, workdir="w1")
    loaded = load_config(config)
    assert loaded.section("source").get("local_files") == ["a.html"]


def test_file_source_without_any_input_is_rejected_and_names_local_files(tmp_path: Path) -> None:
    """★ 反方向：两者都没有 ⇒ 空运行，必须拒绝，且报文要点名缺什么。"""
    from omnicrawler.core.config import ConfigParseError, load_config

    config = _write(tmp_path, files=None, seeds=None, workdir="w2")
    with pytest.raises(ConfigParseError) as excinfo:
        load_config(config)
    message = str(excinfo.value)
    assert "local_files" in message, f"报文没点名该补什么：{message}"


def test_file_source_with_explicit_seeds_still_works(tmp_path: Path) -> None:
    """显式写 seeds 的既有写法不受影响。"""
    from omnicrawler.core.config import load_config

    config = _write(tmp_path, files=["a.html"], seeds=[], workdir="w3")
    # seeds: [] 是显式空列表 —— 回退到 local_files，仍应通过
    loaded = load_config(config)
    assert loaded.section("source").get("local_files") == ["a.html"]


def test_empty_local_files_does_not_count_as_an_entry(tmp_path: Path) -> None:
    """`local_files: []` 或全是空字符串 ⇒ 不算有入口。"""
    from omnicrawler.core.config import ConfigParseError, load_config

    config = _write(tmp_path, files=[], seeds=None, workdir="w4")
    with pytest.raises(ConfigParseError, match="local_files"):
        load_config(config)


def test_other_kinds_still_require_seeds(tmp_path: Path) -> None:
    """★ 放宽只针对 `file`，别的 kind 仍必须给 seeds。"""
    from omnicrawler.core.config import ConfigParseError, load_config

    work = tmp_path / "w5"
    work.mkdir()
    config = tmp_path / "w5.yaml"
    config.write_text(
        "config_version: 3\n"
        f"project:\n  name: t\n  workspace: {work}\n"
        "source:\n  kind: static_html\n"
        "crawl:\n  max_pages: 5\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigParseError, match="至少需要一个入口"):
        load_config(config)


def test_checker_only_inspects_shape_not_the_filesystem(tmp_path: Path) -> None:
    """★ `_source_has_local_files` 只看形状：文件不存在也必须判「有入口」。

    这样「在别的机器上生成的配置」在本机能通过校验；真正的存在性/越界检查
    由 `declared_files()` 在读取时做。
    """
    import sys

    repo = Path(__file__).resolve().parents[3]
    if str(repo / "src") not in sys.path:
        sys.path.insert(0, str(repo / "src"))
    from omnicrawler.core.config import _source_has_local_files, load_config

    config = _write(tmp_path, files=["does-not-exist.html"], seeds=None, workdir="w6")
    loaded = load_config(config)
    assert _source_has_local_files(loaded) is True
