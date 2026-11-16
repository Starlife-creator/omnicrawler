"""条目 5「先做减法」：删掉 chromium_headless_shell，headless 走完整 Chromium。

**为什么删不会削弱功能**

Playwright 1.61.0 驱动（`playwright/driver/package/lib/coreBundle.js`）的选择逻辑：

```js
getExecutableName(options) {
  if (options.channel && registry.isChromiumAlias(options.channel))
    return "chromium";                                    // ← 这一行绕过下面的三元
  ...
  return options.headless ? "chromium-headless-shell" : "chromium";
}
```

而 `chromiumAliases = ["chrome-for-testing"]`。

⇒ **只要 launch 时传 `channel="chrome-for-testing"`，headless 也会用完整 Chromium。**
完整 Chromium 是 headless_shell 的**超集**（后者是它的裁剪版），所以功能不会削弱。
代价是 shell 启动更快、内存更省——那是性能取舍，不是能力缺失。

**本守卫的两条判据**

1. **代码**：所有 `chromium.launch(...)` 调用点都必须显式传 `channel`，
   否则 headless 路径会去找一个我们**不再打包**的二进制 ⇒ 运行期 `Executable doesn't exist`。
2. **构建**：三平台构建脚本必须把 `chromium_headless_shell-*` 从产物里剔除。

第 1 条是**硬性**的：漏掉任何一处，那个调用点在冻结包里必然崩。
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
#: **裁剪发生在 runtime prepare 脚本里**，不是 build_*.ps1/sh 本身：
#: 浏览器由 Playwright `install chromium` 整体装进 BROWSERS_ROOT，
#: 三个平台的 build 脚本都只是调用对应的 prepare_*_runtime 脚本。
#: 因此判据只看 prepare 脚本——要求 build 脚本也出现该标记是错的口径。
_PRUNE_SCRIPTS = (
    "tools/prepare_windows_runtime.ps1",
    "packaging/prepare_linux_runtime.sh",
    "packaging/prepare_macos_runtime.sh",
)


def _launch_call_sites() -> list[tuple[str, int, bool]]:
    """返回 ``(文件, 行号, 是否带 channel)`` 列表。

    ``channel`` 的合法写法守卫都必须认：
      1. 字面量：``channel="chrome-for-testing"``；
      2. **单一来源函数**：``channel=launch_channel()``——推荐写法，
         值由 ``browser_launch.launch_channel()`` 统一提供；
      3. ``**launch_kwargs(...)`` 展开（保留兼容，虽然当前不用）。
    只认第 1 种会把推荐写法误判成"漏了 channel"。
    """
    sites: list[tuple[str, int, bool]] = []
    for path in sorted((_REPO_ROOT / "src" / "omnicrawler").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr not in {"launch", "launch_persistent_context"}:
                continue
            has_channel = False
            for kw in node.keywords:
                if kw.arg == "channel":
                    has_channel = True
                    break
                # ``**launch_kwargs(...)`` 展开：kw.arg 为 None
                if kw.arg is None and isinstance(kw.value, ast.Call):
                    inner = kw.value.func
                    name = getattr(inner, "id", None) or getattr(inner, "attr", "")
                    if name == "launch_kwargs":
                        has_channel = True
                        break
            sites.append(
                (path.relative_to(_REPO_ROOT).as_posix(), node.lineno, has_channel)
            )
    return sites


def test_every_playwright_launch_passes_an_explicit_channel() -> None:
    """每个 launch 调用都必须传 channel —— 否则 headless 会找不存在的 shell。"""
    sites = _launch_call_sites()
    assert sites, "没有找到任何 launch 调用点，判据本身失效（空集合会让断言恒真）"

    offenders = [f"{path}:{line}" for path, line, has_channel in sites if not has_channel]
    assert offenders == [], (
        "以下 Playwright launch 未传 channel："
        + ", ".join(offenders)
        + "\n删掉 chromium_headless_shell 后，headless 路径会去找一个不再打包的二进制，"
        "运行期直接 'Executable doesn't exist'。必须显式传 channel 指向完整 Chromium。"
    )


def test_build_scripts_prune_headless_shell() -> None:
    """三平台的 runtime prepare 脚本都必须把 chromium_headless_shell 剔除。"""
    missing: list[str] = []
    for relative in _PRUNE_SCRIPTS:
        path = _REPO_ROOT / relative
        if not path.is_file():
            missing.append(f"{relative} (缺失)")
            continue
        text = path.read_text(encoding="utf-8")
        if "chromium_headless_shell" not in text:
            missing.append(relative)
    assert missing == [], (
        "以下 prepare 脚本未剔除 chromium_headless_shell：" + ", ".join(missing)
        + "\n浏览器由 Playwright install 整体装进 BROWSERS_ROOT，"
        "裁剪必须发生在那之后的 prepare 阶段。"
    )
