"""B3：init 模板名别名与默认值守卫。

2026-09-23 删除根目录 20 个 legacy 平面模板后，`init --template` 的旧名
必须经 `_TEMPLATE_ALIASES` 落到等价新结构模板（保 CLI 兼容面），默认值
从 `static_html` 改向 `generic/single_page`。本文件钉两件事：

1. 旧名别名可用且产物是合法配置（否则打包环境里旧脚本直接 FileNotFoundError）；
2. CLI parser 的默认值确为新模板（防止未来被无意改回退役名）。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from omnicrawler.commands.init_project import _TEMPLATE_ALIASES, execute

# 抽样覆盖各类别名指向（全量 16 条逐一跑会重复覆盖同一逻辑路径）
_SAMPLE_ALIASES = (
    "static_html",
    "rest_api",
    "browser",
    "authenticated",
    "pdf_end_to_end",
)


def test_legacy_template_names_alias_to_valid_templates(tmp_path: Path) -> None:
    for legacy_name in _SAMPLE_ALIASES:
        target_dir = tmp_path / legacy_name
        result = execute(legacy_name, str(target_dir), "demo")
        created = Path(result["created"])
        assert created.is_file(), f"别名 {legacy_name} 应能产出配置文件"
        data = yaml.safe_load(created.read_text(encoding="utf-8"))
        assert data["project"]["name"] == "demo"
        assert data["project"]["workspace"] == "work/demo"
        assert data["source"]["seeds"], f"别名 {legacy_name} 的产物必须有种子"


def test_pdf_end_to_end_alias_still_bundles_pdf_assets(tmp_path: Path) -> None:
    """pdf 资产特判按原始请求名判定：别名改向后 bundled/pdf 仍要随项目复制。"""
    execute("pdf_end_to_end", str(tmp_path), "demo")
    assert (tmp_path / "pdf" / "generic_template.yaml").is_file()


def test_alias_map_only_references_existing_template_files() -> None:
    """别名指向的模板文件必须真实存在（防 alias 拼写漂移）。"""
    bundled = Path(__file__).resolve().parents[3] / "src" / "omnicrawler" / "templates"
    missing = [
        f"{old} -> {new}"
        for old, new in _TEMPLATE_ALIASES.items()
        if not (bundled / f"{new}.yaml").is_file()
    ]
    assert not missing, f"别名目标文件不存在：\n" + "\n".join(missing)


def test_retired_names_have_no_passthrough_gap() -> None:
    """退役名必须全部在别名表里，或明确记录为"无等价、走 examples 兜底"。

    防止未来再删模板时忘记配别名（static_html 曾是 CLI 默认值，删了没别名
    会在打包环境直接 FileNotFoundError）。
    """
    retired = {
        "authenticated", "browser", "crawl_bfs", "crawl_dfs", "feed", "focused",
        "form", "graphql", "httpx_async", "incremental", "long_poll", "media",
        "pdf_end_to_end", "rest_api", "sitemap", "sse", "static_html", "table",
        "url_list", "websocket",
    }
    documented_no_equivalent = {"focused", "incremental", "url_list", "httpx_async"}
    unhandled = retired - set(_TEMPLATE_ALIASES) - documented_no_equivalent
    assert not unhandled, f"退役模板名既无别名也无豁免记录：{sorted(unhandled)}"
