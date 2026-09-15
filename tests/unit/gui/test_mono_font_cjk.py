"""等宽字体的 CJK 覆盖：**机检**替代人工截图确认（W6.5 / §5.3 #10）。

## 为什么需要它

`FONT_FAMILY_MONO` 原来的候选全是**纯拉丁**等宽字体（JetBrains Mono / Consolas /
Cascadia Code / Menlo），一旦等宽控件里出现中文（YAML 编辑器、转换日志、差异视图、
复核表格……）就会落到 tofu（缺字形方块）。此前只能靠**人工截图**在某个平台上确认，
换个系统就没人知道。

## ★ 环境事实（2026-09-15 实测，别写错）

本机 venv 是 **full 版**（`.[full]` 的 18 个可选依赖全部装上）。这里出现的"零字体"**不是**
安装退化，而是 **Qt 平台插件**的属性：

| 运行方式 | `QFontDatabase.families()` |
|---|---|
| `QT_QPA_PLATFORM=offscreen`（测试套件默认） | **0**（该插件不枚举系统字体） |
| 真实平台（本机 windows） | **355** |

所以动态层跑两条路：**进程内**（当前插件，可能是 offscreen ⇒ 只能可见跳过）与
**子进程去掉 `QT_QPA_PLATFORM`**（真实字体栈 ⇒ 本机 full 版能真验）。后者是重点：
否则在 offscreen 下永远只能跳过，等于把完整环境白白浪费掉。

* 第一层（静态、任何环境可判）：**声明里必须有 CJK 等宽候选** —— 少了就是缺陷；
* 第二层（静态）：拆列表形状正确（无空项、末位是通用 `monospace`）；
* 第三层（守卫）：源码里不得把**族列表**交给 `setFontFamily` —— Qt 会把它当成**一个**字体名，
  等宽与 CJK 回退双双静默失效（这正是本次修掉的写法）；
* 第四层（动态）：有字体的环境里等宽 `QFont` 必须**渲染得出**汉字（进程内 + 真实平台各一份）。

## 为什么不设"反向探针"

曾经写过一条"不存在的字体族必须渲染不出汉字"的反向用例，用来证明 `inFont` 有区分力。实测后**删掉**：

* **macOS**：未知字体族会被**回退解析到真实字体**并逐字形回退 ⇒ 该用例在 CI 上判红；
* **Windows（355 个字体，真实平台）**：`inFont` 对**任何**码位都返回 `True` —— 连非字符
  `U+FFFE` / `U+10FFFE`、私用区 `U+E000` 也是 `True`。

也就是说 `QFontMetrics.inFont` **只能证「有」、不能证「无」**，用它构造"无字形"场景不可移植。
判据的**区分力改由第一、三层承担**（都已做承重性核对：把 CJK 候选删掉、或把族列表交回
`setFontFamily`，都会判红）。
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None, reason="GUI 测试需要 PySide6"
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_ROOT = _REPO_ROOT / "src"

_APP = None


def _app():
    global _APP
    if _APP is None:
        from PySide6.QtWidgets import QApplication

        _APP = QApplication.instance() or QApplication([])
    return _APP


# ── 第一层：静态（任何环境都可判）──────────────────────────────────────────


def test_mono_family_declares_a_cjk_capable_candidate() -> None:
    """等宽字体族里**必须**有能覆盖 CJK 的候选，否则有字形的系统上也白搭。"""
    from omnicrawler.gui.design_system import CJK_CAPABLE_MONO_FAMILIES, mono_font_families

    families = mono_font_families()
    candidates = [name for name in families if name in CJK_CAPABLE_MONO_FAMILIES]
    assert candidates, (
        f"等宽字体族里没有 CJK 回退候选（现有：{families}）。"
        f"中文会渲染成 tofu；请把 {list(CJK_CAPABLE_MONO_FAMILIES)} 中至少一个加进 "
        f"FONT_FAMILY_MONO（W6.5）。"
    )


def test_mono_family_list_is_well_formed() -> None:
    """拆列表：无空项、顺序与声明一致、末位是通用族名（交给平台兜底）。"""
    from omnicrawler.gui.design_system import FONT_FAMILY_MONO, mono_font_families

    families = mono_font_families()
    assert families == [part.strip() for part in FONT_FAMILY_MONO.split(",")]
    assert all(families), families
    assert families[-1] == "monospace", families


def test_source_does_not_pass_a_family_list_to_set_font_family() -> None:
    """守卫：不得把逗号串交给 `setFontFamily`（Qt 会当成一个字体名 ⇒ 回退链静默失效）。"""
    offenders: list[str] = []
    for path in sorted(_SRC_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or "setFontFamily(" not in stripped:
                continue
            if "FONT_FAMILY_" in stripped:
                offenders.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}")
    assert not offenders, (
        f"这些地方把字体**族列表**当单个族名用了：{offenders}。"
        f"QSS 的 `font-family: a, b, c` 支持回退，但 `QFont.setFontFamily` 不支持 —— "
        f"请改用 `font.setFamilies(mono_font_families())`（W6.5）。"
    )


def test_widget_font_carries_the_whole_family_list() -> None:
    """给控件设字体时，**控件上的字体**必须带着完整族列表（含 CJK 候选）。

    这是 convert_tool 里那个真实缺陷的控件级回归：`setFontFamily("a, b, c")` 之后
    控件字体的族列表只有那一串"假名字"，CJK 回退无从谈起。
    本检查不依赖平台是否装字体 —— 只看我们设进去的族列表（任何环境都能跑）。
    """
    from PySide6.QtWidgets import QTextEdit

    from omnicrawler.gui.design_system import CJK_CAPABLE_MONO_FAMILIES, mono_font_families

    _app()
    widget = QTextEdit()
    font = widget.font()
    font.setFamilies(mono_font_families())
    widget.setFont(font)
    families = widget.font().families()
    assert families == mono_font_families(), families
    assert any(name in CJK_CAPABLE_MONO_FAMILIES for name in families), families
    widget.deleteLater()


# ── 第四层：动态（进程内 + 真实平台）──────────────────────────────────────


def test_mono_font_renders_cjk_in_process() -> None:
    """进程内：当前平台插件若枚举得到字体，等宽 `QFont` 必须能渲染汉字。

    套件默认 `QT_QPA_PLATFORM=offscreen`，**该插件不枚举系统字体**（本机 full 版实测 0 个）
    ⇒ 这里会可见跳过并说明；真实字体栈由下一条用例在子进程里验。
    """
    from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics

    from omnicrawler.gui.design_system import CJK_CAPABLE_MONO_FAMILIES, mono_font_families

    _app()
    available = set(QFontDatabase.families())
    if not available:
        pytest.skip(
            f"Qt 平台插件 {os.environ.get('QT_QPA_PLATFORM', '(默认)')!r} 不枚举系统字体"
            "（本机是 full 版、字体齐全）⇒ 进程内无法判定；真实平台见下一条用例"
        )

    installed = [name for name in CJK_CAPABLE_MONO_FAMILIES if name in available]
    if not installed:
        pytest.skip(f"平台未安装 CJK 等宽候选（候选：{list(mono_font_families())}）⇒ 无法判定")

    font = QFont()
    font.setFamilies(mono_font_families())
    metrics = QFontMetrics(font)
    assert metrics.inFont("汉"), (
        f"平台已装 CJK 等宽字体 {installed}，等宽控件却渲染不出汉字 ⇒ 字体栈解析有问题"
    )
    assert metrics.inFont("A"), "等宽字体连拉丁字母都渲染不出，说明字体根本没解析成功"


#: 子进程探针：在**去掉 `QT_QPA_PLATFORM`** 的环境里试真实字体栈，输出一行 JSON。
_REAL_PLATFORM_PROBE = textwrap.dedent(
    """
    import json
    from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics
    from PySide6.QtWidgets import QApplication

    app = QApplication([])
    from omnicrawler.gui.design_system import mono_font_families

    families = QFontDatabase.families()
    font = QFont()
    font.setFamilies(mono_font_families())
    metrics = QFontMetrics(font)
    print(json.dumps({
        "families": len(families),
        "han": bool(metrics.inFont("汉")),
        "latin": bool(metrics.inFont("A")),
        "cjk_candidates": [name for name in mono_font_families() if name in set(families)],
    }))
    """
).strip()


def test_mono_font_renders_cjk_on_the_real_platform() -> None:
    """子进程（真实平台）：本机装了字体就该渲染得出汉字 —— 让"完整环境"真的被用上。

    为什么要另起进程：GUI 套件默认 offscreen，而该插件不枚举系统字体 ⇒ 进程内只能跳过。
    去掉该环境变量后走真实字体栈；**无显示环境**（headless CI）会初始化失败，
    此时**可见跳过并说明**（那是环境没有显示，不是字体栈缺陷）。
    """
    env = {key: value for key, value in os.environ.items() if key != "QT_QPA_PLATFORM"}
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _REAL_PLATFORM_PROBE],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=90,
        )
    except subprocess.TimeoutExpired:
        pytest.skip("真实平台探针超时（无显示环境？）⇒ 无法判定")

    if proc.returncode != 0:
        lines = (proc.stderr or proc.stdout or "").strip().splitlines()
        pytest.skip(f"真实平台无法初始化（headless？）：{(lines[-1] if lines else '(无输出)')[:120]}")

    payload = next(
        (line for line in reversed((proc.stdout or "").splitlines()) if line.strip().startswith("{")),
        "",
    )
    assert payload, f"探针没有输出 JSON：{proc.stdout!r} / {proc.stderr!r}"
    data = json.loads(payload)
    if not data["families"]:
        pytest.skip("真实平台下 Qt 仍枚举不到字体 ⇒ 无法判定字形覆盖")
    assert data["latin"], "真实平台下等宽字体连拉丁字母都渲染不出 ⇒ 字体栈解析有问题"
    assert data["han"], (
        f"真实平台有 {data['families']} 个字体、CJK 候选 {data['cjk_candidates'] or '未安装'}，"
        f"等宽控件却渲染不出汉字 ⇒ 回退链没生效"
    )
