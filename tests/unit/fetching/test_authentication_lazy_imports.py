"""`fetching.authentication` 不许在导入期拉起抽取引擎（2026-11-28）。

## 缺陷

`fetching/authentication.py` 顶端写着 `from ..extraction.extractors import decode_body`
与 `from ..extraction.html_tools import parse_html, select_nodes`，但这两个只在
`auth_check.selector` 存在时用得到（`check_authentication` 里的一个分支）。

而本模块被 `runtime/recovery` → `gui/views/login_session` 拉进 **GUI 启动路径**，
于是「没配 auth_check 的用户」也在启动时付 bs4 的钱。

实测（子进程多次取最小值）：

| 导入 | 修前 | 修后 |
|---|---|---|
| `gui.views.login_session` | 435 ms | **354 ms（−19%）** |
| `gui.main`（整体）| 583 ms | **497 ms（−15%）** |

而且修后 `bs4` 完全不在 `sys.modules` 里。

## 判据

「用不用得上」按**配置**决定，不是按模块划分。`auth_check` 默认是 `{}`
（`config_defaults.py`），绝大多数配置没有 selector ⇒ 绝大多数启动不需要 DOM。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]


def _run_probe(code: str) -> tuple[int, str]:
    """在全新解释器里跑，返回 (returncode, stdout+stderr)。

    必须用子进程：测试进程里早就 import 过 bs4，`sys.modules` 断言恒真。
    """
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=_REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"},
    )
    return result.returncode, ((result.stdout or "") + (result.stderr or "")).strip()


def _probe(code: str) -> str:
    code_rc, out = _run_probe(code)
    assert code_rc == 0, out[-400:]
    return out


def test_authentication_does_not_pull_the_extraction_engine() -> None:
    """★ 核心判据：导入认证模块不许拉起 `extraction.*`。"""
    out = _probe(
        "import sys; import omnicrawler.fetching.authentication; "
        "print(any(k.startswith('omnicrawler.extraction') for k in sys.modules))"
    )
    assert out == "False", "authentication 又在导入期拉起了抽取引擎"


def test_authentication_does_not_pull_bs4() -> None:
    """★ 同类：bs4 是 `extraction.html_tools` 的下游，一并断言。"""
    out = _probe(
        "import sys; import omnicrawler.fetching.authentication; "
        "print('bs4' in sys.modules)"
    )
    assert out == "False"


def test_login_session_view_does_not_pull_bs4() -> None:
    """★ 端到端：GUI 登录会话页的导入不该付 DOM 解析的钱。

    这条链是 `login_session` → `runtime.recovery` → `fetching.authentication`。
    """
    out = _probe(
        "import sys; import omnicrawler.gui.views.login_session; "
        "print('bs4' in sys.modules)"
    )
    assert out == "False"


def test_selector_branch_loads_the_engine_at_call_time() -> None:
    """★ 反方向：真的走到 selector 分支时，`extraction` 必须在**调用点**被加载。

    惰性化不能变成「功能消失」—— 配了 `auth_check.selector` 的用户仍必须能用。

    ★ 语义澄清（读源码确认，不是猜）：`selector` 描述的是**登录页的特征**：

    ```python
    if not select_nodes(...):  return      # 没命中 -> 不是登录页 -> 不判过期
    raise SessionExpiredError(...)         # 命中   -> 就是登录页 -> 会话过期
    ```

    所以「命中」的期望结果是抛 `SessionExpiredError`，**不是**静默通过。
    我第一版把期望写反了，测试当场红 —— 那次失败是对的。

    这里两种结果都接受，只断言**抽取引擎确实在调用点被加载了**。
    """
    code = (
        "import sys\n"
        "from omnicrawler.core.models import CrawlRequest, FetchResult\n"
        "from omnicrawler.fetching.authentication import check_authentication\n"
        "from omnicrawler.core.errors import SessionExpiredError\n"
        "class Cfg:\n"
        "    def section(self, name):\n"
        "        if name == 'source':\n"
        "            return {'auth_check': {'selector': 'div.logged-in'}}\n"
        "        return {}\n"
        "req = CrawlRequest('https://example.com/')\n"
        "res = FetchResult(\n"
        "    request=req,\n"
        "    final_url='https://example.com/home',\n"
        "    status=200,\n"
        "    headers={'content-type': 'text/html; charset=utf-8'},\n"
        "    body=b'<html><body><div class=\"logged-in\">ok</div></body></html>',\n"
        "    elapsed_seconds=0.01,\n"
        ")\n"
        "try:\n"
        "    check_authentication(Cfg(), res)\n"
        "except SessionExpiredError:\n"
        "    pass\n"
        "print(any(k.startswith('omnicrawler.extraction') for k in sys.modules))\n"
    )
    rc, out = _run_probe(code)
    assert rc == 0, f"selector 分支炸了：{out[-500:]}"
    assert out.strip().endswith("True"), (
        f"走到 selector 分支却没在调用点加载抽取引擎：{out[-300:]}"
    )


def test_missing_selector_leaves_the_engine_unloaded() -> None:
    """★ 反过来：`auth_check` 只有 status_codes 时**不该**加载抽取引擎。

    这条才是「惰性真的生效」的证据 —— 上一条只证明「要用时能加载」。
    """
    code = (
        "import sys\n"
        "from omnicrawler.core.models import CrawlRequest, FetchResult\n"
        "from omnicrawler.fetching.authentication import check_authentication\n"
        "from omnicrawler.core.errors import SessionExpiredError\n"
        "class Cfg:\n"
        "    def section(self, name):\n"
        "        if name == 'source':\n"
        "            return {'auth_check': {'status_codes': [401]}}\n"
        "        return {}\n"
        "req = CrawlRequest('https://example.com/')\n"
        "res = FetchResult(request=req, final_url='https://example.com/', status=200,\n"
        "                  headers={}, body=b'x', elapsed_seconds=0.01)\n"
        "try:\n"
        "    check_authentication(Cfg(), res)\n"
        "except SessionExpiredError:\n"
        "    pass\n"
        "print(any(k.startswith('omnicrawler.extraction') for k in sys.modules))\n"
    )
    rc, out = _run_probe(code)
    assert rc == 0, f"status_codes 分支炸了：{out[-500:]}"
    assert out.strip().endswith("False"), f"没用 selector 却拉了抽取引擎：{out[-300:]}"


def test_probe_can_actually_detect_bs4() -> None:
    """★ 自检：探针必须能看见 bs4 —— 否则上面的 False 是假绿。"""
    out = _probe("import sys, bs4; print('bs4' in sys.modules)")
    assert out == "True"
