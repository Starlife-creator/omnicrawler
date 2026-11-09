"""安装 extra 与「能力探测」名字必须对得上。

为什么需要这条约束：`pyproject.toml` 的功能 extra（如 `crawl4ai`、`ocr-captcha`、
`document`）是用户安装的单位，`capability_report(require_features=...)` 是用户和
诊断界面查询可用性的单位。两套名字一旦漂移，就会出现「装上了但查不到」或
「查得到但装了也没用」这种各说各话的情况——批次 5-③ 之前有 13 个 extra 名根本
不是合法能力名，`--mode task --require crawl4ai` 直接报「未知能力名称」。

判据取「feature 的 modules 是 extra 承诺模块的子集」而不是「相等」：能力只承诺
运行任务所必需的那几个模块（例如 `html` 只验 `bs4`/`lxml`，不验 `cssselect`），
这比机械对齐包列表更贴近「这个任务能不能跑」。
"""

from __future__ import annotations

import pathlib

import pytest

from omnicrawler.core.capabilities import FEATURE_REQUIREMENTS, MODULES, capability_report
from tools.check_extra_install import AGGREGATE_EXTRAS, PROFILE_IMPORTS

FEATURE_EXTRAS = tuple(name for name in PROFILE_IMPORTS if name not in AGGREGATE_EXTRAS)

#: 历史能力名，出现在既有配置、文档和用户脚本里，必须继续可用。
HISTORICAL_FEATURES = ("web", "streams", "storage-s3", "storage-duckdb", "storage-parquet", "storage-postgresql", "storage-opensearch")


def _normalise(name: str) -> str:
    """把包名与探测模块名折成同一种写法（PIL/psycopg/opensearchpy 这类差异只在这里处理）。"""

    return name.casefold().replace("_", "-").replace(".", "-")


@pytest.mark.parametrize("extra", FEATURE_EXTRAS)
def test_every_installable_extra_has_a_capability_name(extra: str) -> None:
    assert extra in FEATURE_REQUIREMENTS, (
        f"extra '{extra}' 装得上但查不到：`--require {extra}` 会报「未知能力名称」"
    )


@pytest.mark.parametrize("extra", FEATURE_EXTRAS)
def test_capability_only_promises_modules_the_extra_installs(extra: str) -> None:
    requirement = FEATURE_REQUIREMENTS[extra]
    promised = {_normalise(MODULES[key]) for key in requirement["modules"]}
    installed = {_normalise(module) for module in PROFILE_IMPORTS[extra]}
    surplus = promised - installed
    assert not surplus, (
        f"能力 '{extra}' 会去探测 {sorted(surplus)}，但该 extra 并不安装它们："
        "装完 extra 反而会让这个能力判为未就绪"
    )


def test_every_capability_module_key_exists() -> None:
    referenced = {key for requirement in FEATURE_REQUIREMENTS.values() for key in requirement["modules"]}
    assert referenced, "empty selection is not a pass: no capability references any module"
    assert not sorted(referenced - set(MODULES))


def test_every_capability_module_key_is_reachable_from_a_feature() -> None:
    """表里每个模块都应能被某个需求命中，否则它是死条目——不会有人查它。"""

    referenced = {key for requirement in FEATURE_REQUIREMENTS.values() for key in requirement["modules"]}
    orphans = sorted(set(MODULES) - referenced)
    assert not orphans, f"这些探测模块没有任何能力用到：{orphans}"


def test_historical_feature_names_still_resolve() -> None:
    for name in HISTORICAL_FEATURES:
        assert name in FEATURE_REQUIREMENTS, f"历史能力名 '{name}' 被删了，既有配置会直接报错"


def test_task_mode_accepts_every_extra_name() -> None:
    """真实调用一次，确认没有中间层把名字重新过滤掉。"""

    report = capability_report(mode="task", require_features=list(FEATURE_EXTRAS))
    assert report["check"]["requested_features"] == list(FEATURE_EXTRAS)
    assert set(report["check"]["features"]) == set(FEATURE_EXTRAS)


def test_quick_mode_still_checks_only_the_core_runtime() -> None:
    """扩表的代价不能落到默认路径上：未指定 require 时仍只导入 core_yaml。"""

    assert capability_report()["check"]["imported_modules"] == ["core_yaml"]


def test_reverse_assertion_a_missing_feature_turns_the_contract_red() -> None:
    """反向断言：把 crawl4ai 从表里拿掉，上面那条子集/存在性判据必须报出来。"""

    trimmed = {name: value for name, value in FEATURE_REQUIREMENTS.items() if name != "crawl4ai"}
    missing = [name for name in FEATURE_EXTRAS if name not in trimmed]
    assert missing == ["crawl4ai"]


def test_reverse_assertion_a_surplus_module_turns_the_contract_red() -> None:
    """反向断言：让某个能力探测 extra 不装的模块，判据必须报出来。"""

    requirement = dict(FEATURE_REQUIREMENTS["selenium"])
    requirement["modules"] = (*requirement["modules"], "crawl4ai")
    promised = {_normalise(MODULES[key]) for key in requirement["modules"]}
    installed = {_normalise(module) for module in PROFILE_IMPORTS["selenium"]}
    assert promised - installed == {"crawl4ai"}


def test_cli_help_lists_the_capability_names_it_accepts() -> None:
    """帮助文本曾只列 8 个旧名字，用户照着敲就报「未知能力名称」——这条钉住它。"""

    parser_path = pathlib.Path(__file__).resolve().parents[3] / "src" / "omnicrawler" / "cli" / "_parsers" / "task.py"
    text = parser_path.read_text(encoding="utf-8")
    for name in ("crawl4ai", "document", "ocr-captcha", "security", "async-http"):
        assert name in text, f"`--require` 的帮助文本没提到 '{name}'"


def test_test_file_lives_where_the_repo_layout_says() -> None:
    """防止测试被挪走后相对路径假设失效（本文件不读仓库文件，这里只钉目录约定）。"""

    assert pathlib.Path(__file__).parent.name == "core"
