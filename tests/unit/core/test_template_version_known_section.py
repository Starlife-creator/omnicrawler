"""随包模板写出的 template_version 不得被判为「未知顶层段」。

为什么要有这个文件（2026-11-25 端到端冒烟实测）：

init --template 生成的配置开头是：

    template_version: 1
    template:
      id: generic/single-page

而 validate_config 的顶层段白名单里有 template 却没有 template_version，
于是用户拿到配置后第一件事 validate 就收到：

    配置包含未知顶层段 template_version

这不是用户拼错 —— 是**我们自己的 50 个随包模板**（gui/templates/*.yaml、
templates/scenarios/*.yaml 等）全都写入了它。开箱第一条警告就是假的。

本文件钉住：模板形态的配置校验后不含「未知顶层段」告警；
同时真拼写错误仍要报（白名单不能放太宽）。
"""

from __future__ import annotations

from pathlib import Path

from omnicrawler.core.config import load_config, validate_config


def _write_template_config(path: Path) -> None:
    path.write_text(
        "template_version: 1\n"
        "template:\n"
        "  id: generic/single-page\n"
        "  name: generic\n"
        "  category: generic\n"
        "  version: 1.0.0\n"
        "project:\n"
        "  name: demo\n"
        "  workspace: work/demo\n"
        "source:\n"
        "  kind: static_html\n"
        "  seeds:\n"
        "  - https://example.com/\n"
        "extract:\n"
        "  mode: html\n"
        "  item_selector: body\n"
        "  fields:\n"
        "    title:\n"
        "      selector: h1\n",
        encoding="utf-8",
    )


def test_template_version_not_flagged_as_unknown(tmp_path: Path) -> None:
    """template_version 不在「未知顶层段」告警里。"""
    config_path = tmp_path / "tpl.yaml"
    _write_template_config(config_path)
    config = load_config(config_path)

    errors, warnings = validate_config(config)
    assert not errors, errors
    offending = [w for w in warnings if "未知顶层段" in w and "template_version" in w]
    assert not offending, "模板写出的 template_version 被当成未知段了：" + repr(offending)


def test_real_typo_still_flagged(tmp_path: Path) -> None:
    """真拼写错误仍要报 —— 白名单不能因为放宽而失去检查能力。"""
    config_path = tmp_path / "bad.yaml"
    config_path.write_text(
        "project:\n"
        "  name: demo\n"
        "  workspace: work/demo\n"
        "source:\n"
        "  kind: static_html\n"
        "  seeds: [https://example.com/]\n"
        "templete_version: 1\n",
        encoding="utf-8",
    )
    config = load_config(config_path)
    errors, warnings = validate_config(config)
    assert any("templete_version" in w for w in warnings), "真拼写错误没被报出来"
