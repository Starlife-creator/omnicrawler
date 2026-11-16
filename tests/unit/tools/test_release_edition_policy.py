"""条目 13：冒烟测试不得用「版本名」当能力判据。

**停发 Full 后会直接失效的地方**

`tools/portable_smoke_test.py` 里有两处用 `edition == "Full"` 决定要不要跑：

```
Line 298:  if edition == "Full":   → 跑 capabilities --self-test（OCR 自检）
Line 282/290: elif edition == "Full" and <平台>:  → 跳过 selenium 引擎冒烟
```

停发 Full 后，Standard 会成为**唯一**发布的版本。如果判据仍是
"版本名 == Full"，就会有两个后果，**都是错的**：

1. **OCR 自检永远不跑**。Standard 里其实**可以有** OCR（用户按需装组件
   或本机已有 Tesseract/Paddle），但判据看的是版本名 ⇒ 这块永久失去覆盖。
2. **selenium 的已知平台 bug 注释失去意义**：那段注释解释的是"为什么
   Full 跳过 selenium"，若 Standard 变成默认且不跳过，就会在 CI 里
   撞上同一个 BiDi 超时——而看起来像是新引入的故障。

**正确判据是「能力是否具备」，不是「版本叫什么」**：

* OCR 自检 ⇒ 当且仅当产物**确实带 OCR 能力**时跑（否则跳过并说明原因）；
* 浏览器引擎 ⇒ 当且仅当产物**确实带该引擎**时跑。

**判据（本守卫钉住三条）**

1. 源码里不得再用 `edition == "Full"` 作为"要不要跑某项检查"的**唯一**条件；
2. 能力探测必须**可解释**：跳过时要说清"缺什么"，不能静默少跑；
3. 停发 Full 的机制（`workflow_dispatch` 选择 + release job 跳过）必须存在，
   但 **CI 仍构建 Full**（"停发 ≠ 停构建"）。
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SMOKE = _REPO_ROOT / "tools" / "portable_smoke_test.py"
_WORKFLOWS = _REPO_ROOT / ".github" / "workflows"


def test_smoke_does_not_gate_ocr_selftest_on_edition_name() -> None:
    """OCR 自检不得只按 ``edition == "Full"`` 判断。"""
    text = _SMOKE.read_text(encoding="utf-8")
    offending = [
        (index, line.strip())
        for index, line in enumerate(text.splitlines(), 1)
        if re.search(r'if\s+edition\s*==\s*["\']Full["\']\s*:\s*$', line)
    ]
    assert offending == [], (
        "冒烟测试仍用 `edition == 'Full'` 作为唯一条件决定是否跑 OCR 自检：\n  "
        + "\n  ".join(f"{n}: {line}" for n, line in offending)
        + "\n停发 Full 后 Standard 是唯一发布版本，这块会永久失去覆盖。"
        "请改为按「产物是否具备 OCR 能力」判断，并在跳过时说明原因。"
    )


def test_smoke_has_capability_probe() -> None:
    """冒烟测试必须提供「能力探测」函数，而不是只看版本名。"""
    text = _SMOKE.read_text(encoding="utf-8")
    assert re.search(r"def\s+_?detect_\w*capabilit", text) or "_probe_capabilities" in text, (
        "冒烟测试没有能力探测函数；停发 Full 后无法区分"
        "「Standard 没有 OCR」与「Standard 有 OCR 但没测」"
    )


def test_skip_reasons_are_explicit() -> None:
    """跳过某项检查时必须打印原因——静默少跑等于没有覆盖。"""
    text = _SMOKE.read_text(encoding="utf-8")
    # 允许的形态：skip 提示里带原因词
    skips = re.findall(r"(?:skip|跳过)[^\n]*", text, flags=re.IGNORECASE)
    assert skips, "找不到任何跳过提示——是否在静默跳过检查？"
    explained = [s for s in skips if any(k in s for k in ("无", "缺", "不支持", "原因", "not ", "no "))]
    assert explained, (
        "跳过提示没有说明原因，用户/CI 无法判断是「不该跑」还是「忘了跑」："
        f"{skips[:3]}"
    )


def test_full_build_still_exists_in_ci() -> None:
    """**停发 ≠ 停构建**：CI 仍必须构建 Full（只是不打 tag/不发布）。"""
    found: list[str] = []
    for workflow in sorted(_WORKFLOWS.glob("*.yml")):
        text = workflow.read_text(encoding="utf-8")
        if "Full" in text and ("build" in text.lower() or "edition" in text.lower()):
            found.append(workflow.name)
    assert found, "CI 里找不到任何构建 Full 的痕迹——停发不该等于停构建"


def test_release_job_can_skip_full() -> None:
    """发布环节必须能**跳过** Full（保留构建能力，但不产出 release）。"""
    candidates = [
        (_WORKFLOWS / "release.yml"),
        (_WORKFLOWS / "finalize" / "reusable-finalize-release.yml"),
        (_WORKFLOWS / "reusable-finalize-release.yml"),
    ]
    texts = {path.name: path.read_text(encoding="utf-8") for path in candidates if path.is_file()}
    assert texts, "找不到发布工作流"
    joined = "\n".join(texts.values())
    assert ("skippedDir" in joined) or ("skip" in joined.lower() and "full" in joined.lower()), (
        "发布工作流没有「跳过 Full」的机制；停发只能靠人工不点，"
        "很容易误发"
    )


# ── 能力探测本身必须可用 ─────────────────────────────────────────────────


def test_capability_probe_does_not_pass_unknown_flag() -> None:
    """探测命令不得传 ``capabilities`` 不认识的参数。

    **这是一个真实踩到的坑**：第一版写的是 ``capabilities --json``，
    而该子命令**没有** ``--json``（默认就输出 JSON）。结果命令直接报错退出，
    探测恒返回 ``None``，于是两项检查**被静默跳过**——
    冒烟测试「通过」了，其实一大块覆盖没了。

    这类"守卫自己坏了导致假绿"最危险，所以单独钉一条。
    """
    text = _SMOKE.read_text(encoding="utf-8")
    probe_src = text.split("def _probe_capabilities")[1].split("\n\ndef ")[0]
    assert '"--mode"' in probe_src, "探测没有使用 capabilities 支持的参数"
    assert '"--json"' not in probe_src, (
        "探测传了 capabilities 不支持的 --json：命令会报错、探测恒为 None、"
        "后续检查被静默跳过"
    )


def test_capability_available_is_conservative() -> None:
    """能力判据必须保守：读不到一律视为不可用。"""
    import sys
    from pathlib import Path as _Path

    sys.path.insert(0, str(_Path(__file__).resolve().parents[3] / "tools"))
    try:
        from portable_smoke_test import _capability_available
    finally:
        sys.path.pop(0)

    assert _capability_available(None, "ocr") is False
    assert _capability_available({}, "ocr") is False
    assert _capability_available({"modules": {}}, "ocr") is False
    # 真实报告形态：modules 里带 installed
    report = {"modules": {"pytesseract": {"module": "pytesseract", "installed": True}}}
    assert _capability_available(report, "ocr") is False  # 名字里没有 ocr
    report_ocr = {"modules": {"ocr_paddle": {"module": "paddleocr", "installed": True}}}
    assert _capability_available(report_ocr, "ocr") is True
