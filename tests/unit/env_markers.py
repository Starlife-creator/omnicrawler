"""按外部条件跳过测试：把「环境不满足」与「代码有缺陷」分开。

## 这个模块解决什么

本会话结束时全量单测剩 11 条红，我靠人工逐个判断出它们是环境问题：

| 类别 | 条数 | 证据 |
|---|---|---|
| 本机 DNS 被劫持 | 5 | `github.com` 解析到 `127.0.0.1` |
| Windows 沙箱限制 | 4 | bash 报 `NtCreateDirectoryObject(0xC0000022)` |
| 数据目录不可写 | 1 | `.registry.lock` Permission denied |
| 未查清 | 1 | 见 §15.16.15.6 |

问题是：**「11 条红」在统计上和「11 个真 bug」完全无法区分**。
我为这片红花了大量时间，而它们在 CI（网络正常、bash 可用、
数据目录干净）上本来就会通过。

## 为什么不用 `pytest.ini` 的 `-m "not network"` 手写排除

那要求每个测试文件自己记得加标记 —— 漏一个就白做。
这里改成**按条件自动跳过**：环境不满足时，带标记的测试自动 skip，
环境满足时**真跑**（CI 上就是这样）。

判定写在 `conftest.py` 里，用 `pytest_collection_modifyitems` 统一处理，
测试文件只需 `@pytest.mark.requires_network`。

## 三类条件与判据

* `requires_network` —— **能解析且不落在回环/保留地址**。
  只看「有没有网卡」不够：本机 DNS 被劫持到 `127.0.0.1` 时网卡是好的，
  但所有外网请求都会被安全防护拒绝（这是**正确**行为）。
* `requires_bash` —— **真能执行一条命令**。
  `shutil.which("bash")` 会找到 `C:\\Windows\\system32\\bash.EXE`，
  但在受限沙箱里执行会失败，所以必须实际跑一次。
* `requires_writable_data_dir` —— 真实数据目录可写。
  用 `os.access` 判，不写文件（避免副作用）。

## 用法

```python
from tests.unit.env_markers import requires_network

@requires_network
def test_fetches_from_github():
    ...
```
"""
from __future__ import annotations

import ipaddress
import os
import shutil
import socket
import subprocess

import pytest

from tools.child_env import utf8_env

#: 探测用的域名。选 github.com 是因为它同时被自更新与市场客户端使用 ——
#: 这两个正是本会话里因 DNS 劫持而失败的用例。
_PROBE_HOST = "github.com"

#: 判定结果缓存（同一进程只探测一次，避免每个测试都跑一遍）。
_cache: dict[str, bool] = {}


def _is_public_address(address: str) -> bool:
    """地址是否是可路由的公网地址（回环/保留/私有都不算）。"""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return not (
        ip.is_loopback
        or ip.is_private
        or ip.is_reserved
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
    )


def network_available() -> bool:
    """能否真正访问外网。

    **判据是「解析结果不是回环/保留地址」**，不是「有没有网卡」。
    本会话的环境里 `github.com` 解析到 `127.0.0.1`（DNS 被劫持或代理），
    此时安全防护会**正确地**拒绝请求并返回「域名解析到内网或保留地址」。
    那种情况下网络测试必然失败，但不是代码缺陷。
    """
    if "network" in _cache:
        return _cache["network"]
    ok = False
    try:
        address = socket.gethostbyname(_PROBE_HOST)
        ok = _is_public_address(address)
    except OSError:
        ok = False
    _cache["network"] = ok
    return ok


def bash_usable() -> bool:
    """bash 能否**真的执行一条命令**。

    为什么不能只 `shutil.which`：Windows 上总会找到
    `C:\\Windows\\system32\\bash.EXE`（WSL 入口），但在受限沙箱里执行会报
    `NtCreateDirectoryObject(...): 0xC0000022`。必须实际跑一次才算数。
    """
    if "bash" in _cache:
        return _cache["bash"]
    exe = shutil.which("bash")
    ok = False
    if exe:
        try:
            result = subprocess.run(
                [exe, "-c", "echo ok"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                env=utf8_env(),
                check=False,
            )
            ok = result.returncode == 0 and "ok" in (result.stdout or "")
        except (OSError, subprocess.SubprocessError):
            ok = False
    _cache["bash"] = ok
    return ok


def writable_data_dir() -> bool:
    """真实数据目录是否可写。

    某些测试会往 `portable_data_root()` 下写 `.registry.lock`，
    而该目录可能属于另一次安装、当前进程无权写入。
    """
    if "datadir" in _cache:
        return _cache["datadir"]
    ok = False
    try:
        from omnicrawler.core.runtime_paths import portable_data_root

        root = portable_data_root()
        components = root / ".omnicrawler" / "components"
        # ★ 不能只看 `os.access`：本机实测该目录标称可写（`access` 返回 True），
        #   但 `.registry.lock` **独占打开会被拒**（被占用或 ACL 拒绝）。
        #   所以判据是「真的能打开一次」。
        target = components / ".registry.lock"
        if target.is_file():
            with target.open("r+b"):
                pass
            ok = True
        else:
            ok = components.is_dir() and os.access(components, os.W_OK)
    except (OSError, PermissionError):
        ok = False
    except Exception:  # noqa: BLE001 - 判定失败按不可用处理
        ok = False
    _cache["datadir"] = ok
    return ok


#: 需要真正的外网连通（且 DNS 不被劫持）。
requires_network = pytest.mark.skipif(
    not network_available(),
    reason=(
        f"外网不可用：{_PROBE_HOST} 无法解析到公网地址（可能是 DNS 劫持/代理）。"
        "安全防护会正确拒绝内网地址，但测试需要真实外网。"
    ),
)

#: 需要可执行的 bash。
requires_bash = pytest.mark.skipif(
    not bash_usable(),
    reason="bash 不可执行（受限沙箱或未安装）：脚本类测试无法验证",
)

#: 需要可用的 Qt 字体（像素级快照测试的前提）。
#:
#: 本机实测：`QFontDatabase: Cannot find font directory .../PySide6/lib/fonts`，
#: Qt 渲染出来的字形与基准快照不同，`diff_pct` 必然非零。
#: 这不是代码缺陷 —— 换一台装了字体的机器就能过。
#: CI 的视觉回归走**独立 job**（`tools/gates/check_visual_regression.py`），
#: 不依赖这批单元测试。
def qt_fonts_available() -> bool:
    """QFontDatabase 是否至少有一个可用字体族。"""
    try:
        from PySide6.QtGui import QFontDatabase
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
        del app  # 只需构造一次以初始化字体库
        return len(QFontDatabase.families()) > 0
    except Exception:  # noqa: BLE001 - 无 Qt/无显示都视为不可用
        return False


requires_qt_fonts = pytest.mark.skipif(
    not qt_fonts_available(),
    reason="本机没有可用 Qt 字体：像素快照必然与基准不同（环境问题，非缺陷）",
)

#: 需要文档工作台插件（`document-workbench`）。
#:
#: 插件在**独立仓**（`OmniCrawler-market`），e2e job 只 checkout 主仓，
#: 且市场安装**只有 GUI 入口、没有 CLI** —— CI 无法一条命令装好它。
#: 依赖 `pdf` / `convertx` / `document_ir` 的测试都该带上这个标记。
def document_workbench_available() -> bool:
    """文档工作台插件是否可用（用 CLI 自己的判据，避免分歧）。"""
    try:
        from omnicrawler.services.document_facade import document_capability

        return bool((document_capability() or {}).get("available"))
    except Exception:  # noqa: BLE001
        return False


requires_document_workbench = pytest.mark.skipif(
    not document_workbench_available(),
    reason="document-workbench 插件未安装（它在独立仓，CI 无法一条命令装）",
)

#: 需要真实数据目录可写。
requires_writable_data_dir = pytest.mark.skipif(
    not writable_data_dir(),
    reason="真实数据目录不可写：需要写 .omnicrawler 的测试无法验证",
)
