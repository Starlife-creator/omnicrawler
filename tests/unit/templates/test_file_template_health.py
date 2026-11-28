"""`kind: file` 的模板必须能被健康检查通过（2026-11-28）。

## 缺陷

`template_health.validate_template` 无条件要求 `source.seeds` 非空：

```python
if not isinstance(source.get("seeds"), list) or not source.get("seeds"):
    errors.append("source.seeds must contain at least one entry")
```

但 `kind: file` 的入口是 `source.local_files`（`sources/local_files.py` 的
`seed()` 明确支持「seeds 为空 ⇒ 回退到全部 local_files」）。

结果：**一个完全合法的 file 模板被判错**，实测：

```
id: generic/local-files
errors: ("source.seeds must contain at least one entry",)
```

这意味着 **file 类模板写不出来** —— 上一轮刚修好「file 源能跑」，
这一轮发现「file 模板没法存在」。

## 判据与 `core/config.py` 保持同一口径

| 条件 | 结论 |
|---|---|
| 有非空 `seeds` | 通过 |
| 有非空 `local_files`（`kind: file`）| 通过 |
| `kind` 是 `redis` / `scrapy`（自带入口）| 通过 |
| `kind: file` 两者都没有 | 报 "source.local_files must list at least one entry" |
| 其他 kind 两者都没有 | 报 "source.seeds must contain at least one entry" |

★ 只判**形状**，不碰文件系统 —— 模板描述的是「要填什么」，不是「本机有没有」。
"""

from __future__ import annotations

from pathlib import Path

import yaml


def _record(tmp_path: Path, source_block: dict, *, template_id: str = "generic/local-files"):
    """写一个最小模板并让 catalog 解析回来。"""
    from omnicrawler.templates.template_catalog import TemplateCatalog

    config = {
        "template_version": 1,
        "template": {
            "id": template_id,
            "name": "本地文件导入",
            "category": "generic",
            "description": "从本地目录读取明确列出的文件并抽取",
            "version": "1.0.0",
            "tags": ["本地", "导入"],
            "capabilities": ["file-import", "offline"],
            "placeholders": {"input_dir": {"label": "本地目录", "required": True}},
            "verified_at": "2026-11-28",
        },
        "project": {"name": "local_files", "workspace": "work/local_files"},
        "source": source_block,
        "crawl": {"max_pages": 10},
        "extract": {"mode": "auto", "fields": {}},
        "outputs": {"jsonl": True, "csv": True},
    }
    path = tmp_path / "t.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    catalog = TemplateCatalog(tmp_path)
    records = catalog.search()
    assert len(records) == 1, f"模板没解析出来：{catalog.parse_errors}"
    return records[0]


def test_file_kind_with_local_files_is_valid(tmp_path: Path) -> None:
    """★ 核心判据：`kind: file` + `local_files` 的模板必须通过。

    修前这里报 `source.seeds must contain at least one entry` ——
    意味着 **file 类模板根本写不出来**。
    """
    from omnicrawler.templates.template_health import validate_template

    record = _record(
        tmp_path,
        {"kind": "file", "local_root": "{{input_dir}}", "local_files": ["a.html"]},
    )
    health = validate_template(record)
    assert health.errors == (), f"合法 file 模板被判错：{health.errors}"


def test_file_kind_without_any_entry_names_local_files(tmp_path: Path) -> None:
    """★ 反方向：`kind: file` 两者都没有 ⇒ 报错，且报文点名 `local_files`。"""
    from omnicrawler.templates.template_health import validate_template

    record = _record(tmp_path, {"kind": "file", "local_root": "{{input_dir}}"})
    health = validate_template(record)
    assert health.errors, "空 file 模板居然通过了"
    joined = " ".join(health.errors)
    assert "local_files" in joined, f"报文没点名该补什么：{joined}"


def test_seedless_kinds_are_allowed(tmp_path: Path) -> None:
    """`redis` / `scrapy` 自带入口，不需要 seeds 也不需要 local_files。"""
    from omnicrawler.templates.template_health import validate_template

    for kind in ("redis", "scrapy"):
        target = tmp_path / kind
        target.mkdir(parents=True, exist_ok=True)
        record = _record(
            target,
            {"kind": kind},
            template_id=f"protocols/{kind}",
        )
        health = validate_template(record)
        assert health.errors == (), f"{kind} 被判错：{health.errors}"


def test_other_kinds_still_require_seeds(tmp_path: Path) -> None:
    """★ 放宽只针对 `file` 与 seedless kind，别的仍必须给 seeds。"""
    from omnicrawler.templates.template_health import validate_template

    record = _record(
        tmp_path,
        {"kind": "static_html"},
        template_id="generic/no-seeds",
    )
    health = validate_template(record)
    assert health.errors, "缺 seeds 的 static_html 模板居然通过了"
    assert "seeds" in " ".join(health.errors), health.errors


def test_file_template_can_render_and_yield_a_loadable_config(tmp_path: Path) -> None:
    """★ 端到端：file 模板渲染出的配置必须能被 `load_config` 接受。

    这条把「模板校验」与「配置校验」两处口径钉在一起 —— 只改一边会当场红。
    """
    from omnicrawler.core.config import load_config
    from omnicrawler.templates.template_catalog import TemplateCatalog
    from omnicrawler.templates.template_health import validate_template

    record = _record(
        tmp_path,
        {"kind": "file", "local_root": "{{input_dir}}", "local_files": ["a.html"]},
    )
    assert validate_template(record).errors == ()

    workdir = tmp_path / "work"
    workdir.mkdir()
    rendered = TemplateCatalog(tmp_path).render(
        record.metadata.template_id, {"input_dir": str(tmp_path)},
    )
    assert rendered["source"]["local_files"] == ["a.html"]

    out = tmp_path / "rendered.yaml"
    out.write_text(yaml.safe_dump(rendered, allow_unicode=True, sort_keys=False), encoding="utf-8")
    loaded = load_config(out)
    assert loaded.source_kind == "file"

def test_the_shipped_file_template_is_healthy() -> None:
    """★ 出厂模板 `generic/local-files` 本身必须健康（无 error / 无 warning）。

    这是上一条判据的**实际受益者**：模板存在但健康检查不过，等于没有。
    """
    from omnicrawler.templates.template_catalog import TemplateCatalog
    from omnicrawler.templates.template_health import validate_template

    repo = Path(__file__).resolve().parents[3]
    catalog = TemplateCatalog(repo / "src" / "omnicrawler" / "templates")
    records = [r for r in catalog.search() if r.metadata.template_id == "generic/local-files"]
    assert records, "generic/local-files 模板不存在"
    health = validate_template(records[0])
    assert health.errors == (), health.errors
    assert health.warnings == (), health.warnings


def test_the_file_template_renders_a_runnable_config(tmp_path: Path) -> None:
    """★ 端到端：这个模板渲染出的配置**真的能跑出记录**。

    只验 `validate_template` 不够 —— 那只能证明「键写对了」，不能证明
    「`local_files` 的占位符替换后确实指向存在的文件」。
    """
    import yaml as _yaml

    from omnicrawler.core.config import load_config
    from omnicrawler.pipeline import Pipeline
    from omnicrawler.templates.template_catalog import TemplateCatalog

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "a.html").write_text(
        "<html><body><h1>Alpha</h1><p>one</p></body></html>", encoding="utf-8",
    )

    repo = Path(__file__).resolve().parents[3]
    catalog = TemplateCatalog(repo / "src" / "omnicrawler" / "templates")
    rendered = catalog.render(
        "generic/local-files", {"input_dir": str(input_dir), "file_name": "a.html"},
    )
    assert rendered["source"]["local_files"] == ["a.html"]

    config = tmp_path / "c.yaml"
    config.write_text(_yaml.safe_dump(rendered, allow_unicode=True, sort_keys=False), encoding="utf-8")
    result = Pipeline(load_config(config)).run()
    assert result["status"] == "succeeded", result.get("errors")
    assert result["records"] >= 1, f"渲染出的配置没产出记录：{result}"
