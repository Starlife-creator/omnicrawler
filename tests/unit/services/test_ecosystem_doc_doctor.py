"""P3-3 生态清单 doctor 校验钩子测试。

覆盖：相对模块路径→点分模块名映射；真实清单「✅ 已融合」行落点模块全部存在。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from omnicrawl.services.doctor import _module_from_rel_path, check_ecosystem_doc  # noqa: PLC2701


def test_module_from_rel_path_mapping() -> None:
    assert _module_from_rel_path("core/site_aliases.py") == "omnicrawl.core.site_aliases"
    assert _module_from_rel_path("services/progress.py") == "omnicrawl.services.progress"
    assert _module_from_rel_path("convertx/") == "omnicrawl.convertx"
    assert _module_from_rel_path("fetching/async_fetcher") == "omnicrawl.fetching.async_fetcher"
    assert _module_from_rel_path("omnicrawl/quality/normalizers.py") == "omnicrawl.quality.normalizers"
    # 数据文件 / 无意义 token 不参与校验
    assert _module_from_rel_path("b2_domain_mappings_default.yaml") is None
    assert _module_from_rel_path("") is None
    assert _module_from_rel_path("not a path") is None


def test_ecosystem_doc_fused_modules_exist() -> None:
    """真实清单里所有「✅ 已融合」行的落点模块必须可导入（防文档漂移）。"""
    warnings = check_ecosystem_doc()
    assert warnings == [], "\n".join(warnings)


def test_ecosystem_doc_file_exists() -> None:
    import omnicrawl

    doc = Path(omnicrawl.__file__).resolve().parent.parent.parent / "docs" / "ECOSYSTEM_OBSERVATION.md"
    assert doc.is_file(), "缺少 docs/ECOSYSTEM_OBSERVATION.md"
    text = doc.read_text(encoding="utf-8")
    assert "✅ 已融合" in text


def test_doc_fused_module_resolves_individually() -> None:
    """每条已融合路径都能映射到真实模块（与真实清单内容联动的回归防护）。"""
    import re

    import omnicrawl

    doc = (
        Path(omnicrawl.__file__).resolve().parent.parent.parent
        / "docs" / "ECOSYSTEM_OBSERVATION.md"
    )
    checked = 0
    for line in doc.read_text(encoding="utf-8").splitlines():
        if "✅ 已融合" not in line:
            continue
        for token in re.findall(r"`([^`]+)`", line):
            module = _module_from_rel_path(token)
            if module is None:
                continue
            assert importlib.util.find_spec(module) is not None, f"{token} → {module}"
            checked += 1
    assert checked > 0, "清单中应至少有一条已融合项"
