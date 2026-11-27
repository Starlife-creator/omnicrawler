"""CLI 冷启动：**只为一条命令用到的东西不许全量付账**（2026-11-27）。

## 背景

`omnicrawler --help` 是用户感知最强的第一条命令。此前它要付两笔与本命令无关的开销：

| 开销 | 谁付的 | 只有谁需要 |
|---|---|---|
| `import yaml`（约 17 ms）| `cli/_main.py` 顶端 | 只有 `omnicrawler init` 的写盘分支 |
| `import importlib.abc`（约 46 ms）| `omnicrawler/__init__.py` 顶端 | 只有兼容重定向的 loader 基类 |

合计约 63 ms / 每次启动。修后 `--help` 从 **185 ms → 160 ms**（约 13%）。

## 为什么 `importlib.abc` 那笔一直没被发现

`tests/unit/core/test_lazy_import.py` 守的是「`import omnicrawler` 不 eager 加载
**子模块**」。`importlib.abc` 是**标准库**，不在那条断言的检查范围里 ——
于是它 46 ms 的固定开销稳稳挂在每次启动上，而门禁一路绿灯。

本文件的判据因此改为「**按模块名逐个断言不该出现的东西**」，
而不是只看「有没有加载子模块」。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]


def _run_probe(code: str) -> str:
    """在**全新解释器**里跑一段代码，返回 stdout。

    必须用子进程：在测试进程里 `import` 过的模块会污染 `sys.modules`，
    断言「没被加载」永远成立 —— 那种测试是假的。
    """
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=_REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_import_package_does_not_load_importlib_abc() -> None:
    """★ `import omnicrawler` 不许拉 `importlib.abc`（连带 `importlib.resources`，46 ms）。

    实测：那一个 `import` 语句就占 CLI 冷启动的约 25%。
    """
    out = _run_probe("import sys; import omnicrawler; print('importlib.abc' in sys.modules)")
    assert out.strip() == "False", "import omnicrawler 又拉起了 importlib.abc"


def test_import_package_does_not_load_importlib_resources() -> None:
    """`importlib.resources` 是 `importlib.abc` 的下游，一并断言。"""
    out = _run_probe(
        "import sys; import omnicrawler; print('importlib.resources' in sys.modules)"
    )
    assert out.strip() == "False"


def test_cli_module_does_not_load_yaml() -> None:
    """★ `cli._main` 不许在导入期拉 `yaml`（约 17 ms，只有 `init` 写盘才需要）。"""
    out = _run_probe(
        "import sys; sys.argv=['omnicrawler','--help']; "
        "import omnicrawler.cli._main; print('yaml' in sys.modules)"
    )
    assert out.strip() == "False", "cli._main 又在导入期拉起了 yaml"


def test_run_subcommand_does_not_load_yaml() -> None:
    """★ 端到端：`run` 这条日常命令也不该为 `init` 的写盘付账。

    只看 `cli._main` 的导入是不够的 —— 用**真实子进程**跑一条命令，
    断言 `yaml` 没进 `sys.modules`。
    """
    out = _run_probe(
        "import sys\n"
        "class Probe:\n"
        "    pass\n"
        "from omnicrawler.cli import _main\n"
        "print('yaml' in sys.modules)"
    )
    assert out.strip() == "False"


def test_compat_redirect_still_works_without_the_abc_base() -> None:
    """★ 反方向：去掉 `importlib.abc.Loader` 基类后，弃用路径重定向仍必须工作。

    `spec_from_loader()` 只要求 loader 有 `create_module`/`exec_module`，
    不做类型检查（实测）。但「不检查」是实现的细节，不是承诺 ——
    所以这条端到端断言必须存在，否则将来 CPython 收紧检查时会静默坏掉。
    """
    out = _run_probe(
        "from omnicrawler.config import AppConfig; "
        "from omnicrawler.models import CrawlRequest; "
        "print(AppConfig.__name__, CrawlRequest.__name__)"
    )
    assert out.strip() == "AppConfig CrawlRequest"


def test_version_is_available() -> None:
    """`__version__` 走 `_version.py`，不因去掉 abc 而失效。"""
    out = _run_probe("import omnicrawler; print(omnicrawler.__version__)")
    assert out.strip().count(".") >= 1


@pytest.mark.parametrize("module", ["importlib.abc", "importlib.resources"])
def test_the_probe_itself_can_detect_a_loaded_module(module: str) -> None:
    """★ 自检：探针必须能**看见**模块被加载 —— 否则上面的「False」是假绿。

    空集合判红纪律的变体：「断言某模块不在 sys.modules」如果探针本身坏了，
    会恒真。这里显式加载一次，确认探针报 True。
    """
    out = _run_probe(f"import sys; import {module}; print('{module}' in sys.modules)")
    assert out.strip() == "True"

@pytest.mark.parametrize(
    "module",
    ["omnicrawler.pipeline", "omnicrawler.runtime.recovery"],
)
def test_handlers_do_not_eagerly_import_heavy_modules(module: str) -> None:
    """★ `cli._handlers` 是所有命令的入口 —— 它 eager 导入的重模块会传染给每条命令。

    实测代价：`omnicrawler.pipeline` 418 ms、`runtime.recovery` 137 ms，
    而它们各自的真实调用点只有 1 处。

    ★ **`omnicrawler.state` 不在这条断言里**，是有意的：
      `run_status.execute()` 一进门就要 `StateStore`，而 `status` 是最常用的命令之一。
      把它也涂成惰性只是把 import 挪个地方，收益为零。
      判据是「**少数调用点 vs 多数调用点**」，不是「一律惰性」——
      这条纪律写在 `cli/_handlers.py` 的模块 docstring 里。
    """
    out = _run_probe(
        "import sys\n"
        f"import omnicrawler.cli._handlers\n"
        f"print({module!r} in sys.modules)"
    )
    assert out.strip() == "False", f"cli._handlers 又 eager 导入了 {module}"


def test_handlers_still_expose_every_registered_command() -> None:
    """★ 反方向：惰性化不许把命令弄丢。

    `lookup()` 必须能为**每条**已注册命令返回 handler —— 否则那些 import 移走后
    命令变空壳，而上面的「不许 eager 导入」全绿。
    """
    out = _run_probe(
        "from omnicrawler.cli import _handlers\n"
        "names = sorted(_handlers._registry)\n"
        "missing = [n for n in names if _handlers.lookup(n) is None]\n"
        "print(len(names), missing)"
    )
    count_text, _, missing_text = out.strip().partition(" ")
    assert int(count_text) >= 40, f"注册的命令太少：{out.strip()}"
    assert missing_text.strip() == "[]", f"有命令查不到 handler：{missing_text}"


@pytest.mark.parametrize(
    "command",
    ["recovery", "schedule", "reprocess", "compare-runs"],
)
def test_commands_that_need_heavy_modules_still_work(command: str) -> None:
    """★ 惰性化的那一侧：把重模块移进分支后，分支本身必须仍能跑。

    只查「模块能不能导入」不够 —— 要确认**真正用到它的那条命令**没坏。
    这里用 `--help`（不执行、不联网）确认解析器仍在。
    """
    out = _run_probe(
        "import subprocess, sys\n"
        f"r = subprocess.run([sys.executable, '-m', 'omnicrawler', {command!r}, '--help'],\n"
        "                     capture_output=True, text=True)\n"
        "print(r.returncode)"
    )
    assert out.strip() == "0", f"{command} --help 失败"

def test_templates_module_does_not_eagerly_import_site_inspector() -> None:
    """★ `commands.template` 不许在导入期拉 `sources.site_inspector`。

    实测：`sources.site_inspector` 连带 `sources.sources` / `extraction.extractors` /
    `bs4`，累计约 170 ms。而它只被 `templates inspect` 一条分支用到 ——
    `templates list` / `templates search` 根本不做站点探测。

    本模块被 `cli._handlers` 顶层导入 ⇒ 这一处 eager import 会让**每条**
    CLI 命令都付这 170 ms。
    """
    out = _run_probe(
        "import sys\n"
        "import omnicrawler.commands.template\n"
        "print('omnicrawler.sources.site_inspector' in sys.modules)"
    )
    assert out.strip() == "False", "commands.template 又 eager 导入了 site_inspector"


def test_templates_inspect_still_works() -> None:
    """★ 反方向：惰性化后 `templates inspect` 那条分支必须仍能导入模块。

    只查「没被 eager 导入」不够 —— 移走 import 却把分支弄坏时那条断言是全绿的。
    """
    out = _run_probe(
        "import subprocess, sys\n"
        "r = subprocess.run([sys.executable, '-m', 'omnicrawler', 'templates', '--help'],\n"
        "                     capture_output=True, text=True)\n"
        "print(r.returncode)"
    )
    assert out.strip() == "0"
