"""Standard 全装齐 ↔ Full 的能力等价性对账（纯函数，可离线单测）。

## 为什么要对账

停发 Full 后对外只发 Standard。但维护者的要求是：

> 假如说有人全部安装并加载了，那么和现在的 Full 版一模一样。

这句话必须能被**验证**，否则"全装齐等于 Full"只是一句声明。
Full 构建继续在 CI 里跑，正是为了当这个基准。

## 对账的不是"文件集合"，是"能力集合"

同一份能力可能由不同来源提供：

* Standard 内置（`openpyxl` / `reportlab` / `python-docx` / `python-pptx`）
* 市场组件（`ocr-paddle` 的 Wheel-Pack）
* 上游模型（PaddleOCR 权重，`model_download` 拉取）

所以判据是**能力项是否可用**，而不是"哪个包里带的"。

## 三类差异，只允许一种

| 差异 | 是否允许 | 理由 |
|---|---|---|
| Full 有、Standard 全装齐后也有 | ✅ 等价 | 这是目标状态 |
| Full 有、Standard 全装齐也没有 | ❌ **缺口** | 说明有东西没被组件化 |
| Standard 全装齐有、Full 没有 | ⚠️ **超出基准** | 通常意味着 Full 需要更新 |

第三条单独区分，因为"Standard 比 Full 强"不一定是坏事（可能 Full 该升级了），
但**必须被看见**——否则基准本身在悄悄失效。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: 参与对账的能力段（`capability_report` 里的这些段含 `items`）。
_ITEM_SECTIONS = ("standard", "full", "optional")

#: 顶层 `native` 段（**扁平**结构：`{name: {ready, path}}`，不是 `{items: {...}}`）。
#:
#: **为什么必须对账这一段**：它装的正是 Full 相对 Standard 多出来的东西——
#: `chromium` / `selenium_driver` / `tesseract` / `paddle_models`。
#: 早先只对账三段 `items`，等于把「Standard 全装齐后这些原生能力是否齐备」
#: 这个核心问题整个跳过了（实测核实 `capability_report` 的真实结构后发现）。
_NATIVE_SECTION = "native"


@dataclass(frozen=True)
class EquivalenceReport:
    """一次对账的结果。"""

    #: Full 有、Standard 全装齐没有 —— 真正的缺口
    missing_in_standard: tuple[str, ...] = ()
    #: Standard 全装齐有、Full 没有 —— 基准需要更新
    beyond_full: tuple[str, ...] = ()
    #: 两边都有
    common: tuple[str, ...] = ()
    #: 对账时读过多少项（防止"空集合让断言恒真"）
    checked: int = 0
    #: 未能对账的原因（空串表示正常完成）
    note: str = ""

    @property
    def ok(self) -> bool:
        """只在**无缺口**时算通过；"超出基准"不算失败但会被报告。"""
        return not self.missing_in_standard and self.checked > 0


@dataclass
class _Section:
    items: dict[str, Any] = field(default_factory=dict)


def _collect_ready(report: dict[str, Any] | None) -> dict[str, bool]:
    """把能力报告的三个段展开成 `{能力键: 是否就绪}`。

    键名带段前缀（`standard.pdf_text`），避免两段里同名键互相覆盖。
    """
    out: dict[str, bool] = {}
    if not isinstance(report, dict):
        return out

    for section_name in _ITEM_SECTIONS:
        section = report.get(section_name)
        if not isinstance(section, dict):
            continue
        items = section.get("items")
        if not isinstance(items, dict):
            continue
        for key, value in items.items():
            if not isinstance(value, dict):
                continue
            # 不同段用不同字段表示就绪：standard/full 用 ready，optional 用 ready
            ready = value.get("ready")
            if ready is None:
                ready = value.get("installed")
            if ready is None:
                continue
            out[f"{section_name}.{key}"] = bool(ready)

    # 顶层 native 段是**扁平**的（key 直接就是能力名），与上面三段不同。
    native = report.get(_NATIVE_SECTION)
    if isinstance(native, dict):
        for key, value in native.items():
            if not isinstance(value, dict):
                continue
            ready = value.get("ready")
            if ready is None:
                ready = value.get("installed")
            if ready is None:
                continue
            out[f"{_NATIVE_SECTION}.{key}"] = bool(ready)
    return out


def compare_capability_sets(
    full_report: dict[str, Any] | None,
    standard_full_report: dict[str, Any] | None,
) -> EquivalenceReport:
    """对账两份能力报告。

    :param full_report: Full 构建的能力报告（**基准**）
    :param standard_full_report: Standard + 全量组件 + document-workbench 的报告

    两边都只比较**就绪**的能力项：Full 里没就绪的项不要求 Standard 有
    （那说明它在 Full 里本来就缺，不是 Standard 的缺口）。
    """
    if not isinstance(full_report, dict) or not isinstance(standard_full_report, dict):
        return EquivalenceReport(note="能力报告不可读，无法对账")

    full_ready = {k for k, v in _collect_ready(full_report).items() if v}
    standard_ready = {k for k, v in _collect_ready(standard_full_report).items() if v}

    if not full_ready:
        # 空集合会让所有断言恒真 —— 必须显式判为"无法对账"
        return EquivalenceReport(note="Full 基准没有任何就绪能力，对账无意义")

    missing = tuple(sorted(full_ready - standard_ready))
    beyond = tuple(sorted(standard_ready - full_ready))
    common = tuple(sorted(full_ready & standard_ready))

    return EquivalenceReport(
        missing_in_standard=missing,
        beyond_full=beyond,
        common=common,
        checked=len(full_ready),
    )


def render_report(report: EquivalenceReport) -> str:
    """把对账结果渲染成人类可读文本（CI 日志与本地排障共用）。"""
    if report.note:
        return f"无法对账：{report.note}"

    lines = [
        f"对账了 {report.checked} 项 Full 就绪能力",
        f"  共同具备：{len(report.common)} 项",
    ]
    if report.missing_in_standard:
        lines.append(
            f"  ✗ Standard 全装齐后仍缺 {len(report.missing_in_standard)} 项"
            f"（说明有东西没被组件化）："
        )
        lines.extend(f"      - {item}" for item in report.missing_in_standard[:20])
    if report.beyond_full:
        lines.append(
            f"  ⚠ Standard 比 Full 多 {len(report.beyond_full)} 项"
            f"（基准可能需要更新）："
        )
        lines.extend(f"      - {item}" for item in report.beyond_full[:20])
    if report.ok and not report.beyond_full:
        lines.append("  ✓ 完全等价")
    return "\n".join(lines)
