"""`check_guardrails.check_sbom` 的 [4] 判据：**枚举不到环境时不得把 SBOM 全报成"不存在"**。

## 这条守的是一个把发布卡死的真缺陷（2026-09-17 实测）

`v0.13.0` 的 tag 推送后，`release / finalize` 红在：

```
FAIL [4] SBOM 包含环境中不存在的包（30 个）: ['anyio', 'beautifulsoup4', 'certifi', 'cffi',
  'charset-normalizer', 'cryptography', 'cssselect', 'defusedxml']...
```

而 `anyio` / `beautifulsoup4` **本来就在 finalize 所选的 extra 里** —— 列表从字母序最前开始、
数量等于 SBOM 的包数 ⇒ 说明 `frozen` 集合是**空的**：

原实现跑 `subprocess.run([sys.executable, "-m", "pip", "freeze"]).stdout` 且**不检查返回码**，
而 `uv sync` 建的 venv **默认不含 pip** ⇒ stdout 为空 ⇒ 空集 ⇒ **每个包都被判成"不存在"**。

⇒ 修法：改用 `importlib.metadata`（stdlib、不依赖 pip）+ **枚举为空时明确报错**。
本文件守的就是后半条：**判据必须说得出"我没能度量"，而不是把空集当成"一个都不存在"。**
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MODULE_PATH = _REPO_ROOT / "tools" / "check_guardrails.py"


def _module():
    spec = importlib.util.spec_from_file_location("check_guardrails", _MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sbom(tmp_path: Path) -> Path:
    path = tmp_path / "sbom.cdx.json"
    path.write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.5",
                "components": [
                    {"name": "anyio", "version": "4.0.0"},
                    {"name": "beautifulsoup4", "version": "4.12.0"},
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_empty_environment_enumeration_is_reported_as_such(
    tmp_path: Path, monkeypatch
) -> None:
    """★ 枚举为空 ⇒ 必须报"无法枚举"，**不得**报"SBOM 里的包不存在"。"""
    module = _module()
    monkeypatch.setattr(module, "_installed_distributions", lambda: set())
    problems = module.check_sbom(_sbom(tmp_path))
    joined = " | ".join(problems)
    assert any("无法枚举" in p for p in problems), (
        f"枚举为空时必须明确报「无法枚举」，而不是把 SBOM 全报成缺失；实际：{joined}"
    )
    assert not any("环境中不存在" in p for p in problems), (
        f"枚举为空时**不得**断言「包不存在」（那是把空集当成结论）；实际：{joined}"
    )


def test_populated_environment_does_not_mass_report_missing(
    tmp_path: Path, monkeypatch
) -> None:
    """环境里确实有这两包时，[4] 不应报缺失（反向确认判据本身还能正常工作）。"""
    module = _module()
    monkeypatch.setattr(module, "_installed_distributions", lambda: {"anyio", "beautifulsoup4"})
    problems = module.check_sbom(_sbom(tmp_path))
    assert not any("环境中不存在" in p for p in problems), " | ".join(problems)


def test_installed_distributions_does_not_depend_on_pip() -> None:
    """★ 枚举实现不得依赖 `pip`（`uv` 建的 venv 里没有 pip）。"""
    module = _module()
    assert isinstance(module._installed_distributions(), set), "枚举必须返回集合（可为空，但不得抛）"
    source = _MODULE_PATH.read_text(encoding="utf-8")
    start = source.index("def _installed_distributions")
    end = source.index("def check_sbom", start)
    body = source[start:end]
    assert "pip" not in body or "不依赖 pip" in body, (
        "枚举实现里出现了对 pip 的依赖（uv venv 里 pip 不存在 ⇒ 会静默退化成空集）"
    )
    assert "importlib.metadata" in body or "from importlib import metadata" in body, (
        "枚举实现应使用标准库 importlib.metadata"
    )
