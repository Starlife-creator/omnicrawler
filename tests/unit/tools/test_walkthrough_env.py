"""走查启动器的跨平台契约。

**为什么单开一个文件**：这个脚本要给"另一台机器上的另一个开发者"用，而它的两条关键行为
都容易**静默失效**：

1. **数据隔离变量必须按平台** —— 依据 `core/runtime_paths.portable_data_root()` 的实际回落
   （`$LOCALAPPDATA/OmniCrawler`，变量不存在时 `~/.omnicrawler`）：Windows 覆盖
   `LOCALAPPDATA`、POSIX 覆盖 `HOME`。给错变量 ⇒ 数据落进开发者真实目录，且**没有任何报错**。
2. **变量必须作用于 python 那次调用** —— `VAR=... cd ... && python ...` 里变量只对 `cd`
   生效，python 仍用真实 HOME（本脚本第一版就是这个 bug）。
3. **缺可选依赖要降级** —— 不能崩、不能假装可用，要给出补齐命令。

这些都能在单机上验证（`launch_commands` / `capabilities` 是纯函数），不必真的换平台。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
_TOOLS = REPO_ROOT / "tools"


def _load(name: str) -> ModuleType:
    """按路径加载 `tools/` 下的模块（`tools/` 不是包）。"""
    spec = importlib.util.spec_from_file_location(name, _TOOLS / f"{name}.py")
    assert spec and spec.loader, f"无法加载 {name}"
    module = importlib.util.module_from_spec(spec)
    # 启动器里有 `from walkthrough_demo_site import ...`，故需让 tools/ 可导入
    # （幂等插入，避免多次加载时重复累积）
    if str(_TOOLS) not in sys.path:
        sys.path.insert(0, str(_TOOLS))
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def env_module() -> ModuleType:
    return _load("walkthrough_env")


_WIN_PROJECT = Path("C:/wt/round-1/project")
_WIN_DATA = Path("C:/wt/round-1/data")
_POSIX_PROJECT = Path("/home/dev/wt/round-1/project")
_POSIX_DATA = Path("/home/dev/wt/round-1/data")


def test_windows_isolation_uses_localappdata_for_python(env_module: ModuleType) -> None:
    """Windows：隔离靠 `LOCALAPPDATA`，且必须作用在 python 调用上 + 切到项目根。"""
    commands = env_module.launch_commands(_WIN_PROJECT, _WIN_DATA, platform_key="win32")

    assert commands, "Windows 至少要给出一条可用命令"
    for shell, command in commands.items():
        assert "localappdata" in command.lower(), f"{shell} 缺少 LOCALAPPDATA：{command}"
        assert str(_WIN_DATA).replace("\\", "/") in command.replace("\\", "/")
        assert str(_WIN_PROJECT).replace("\\", "/") in command.replace("\\", "/")


def test_posix_isolation_uses_home_on_the_python_invocation(env_module: ModuleType) -> None:
    """POSIX：隔离靠 `HOME`；**变量必须紧贴 python**，否则只作用于 cd（静默失效）。"""
    commands = env_module.launch_commands(_POSIX_PROJECT, _POSIX_DATA, platform_key="linux")

    assert commands
    for shell, command in commands.items():
        assert 'HOME="' in command, f"{shell} 缺少 HOME 隔离：{command}"
        # 关键：`&&` 之后、`python` 之前必须带着 HOME=...（即作用在 python 这次调用上）
        tail = command.split("&&", 1)[-1].strip()
        assert tail.startswith('HOME="'), f"{shell} 的 HOME 没作用在 python 上：{command}"
        assert "python" in tail


def test_capabilities_cover_the_four_walkthrough_needs(env_module: ModuleType) -> None:
    """能力清单必须覆盖走查真正依赖的四项，且"缺"的一项要带补齐命令。"""
    items = env_module.capabilities()
    names = " ".join(item["name"] for item in items)

    for need in ("演示站点", "PDF 附件", "OCR", "中文字体"):
        assert need in names, f"能力清单缺少 {need}：{names}"
    for item in items:
        assert item["ok"] in {"yes", "no"}, item
        if item["ok"] == "no":
            assert item["fix"], f"缺失能力必须给出补齐方式：{item}"


def test_cjk_font_env_override_wins(env_module: ModuleType, tmp_path: Path, monkeypatch) -> None:
    """探测不到时可人工指定字体：`WALKTHROUGH_CJK_FONT` 优先。"""
    fake_font = tmp_path / "custom.ttc"
    fake_font.write_bytes(b"not-a-real-font")
    monkeypatch.setenv(env_module.CJK_FONT_ENV, str(fake_font))

    assert env_module.find_cjk_font() == fake_font


def test_missing_cjk_font_env_override_is_ignored(
    env_module: ModuleType, tmp_path: Path, monkeypatch
) -> None:
    """指定了不存在的路径时按"没指定"处理，不应抛错（降级优先）。"""
    monkeypatch.setenv(env_module.CJK_FONT_ENV, str(tmp_path / "nope.ttc"))

    # 结果取决于本机是否有平台候选字体；只要求不抛异常且返回 Path | None
    result = env_module.find_cjk_font()
    assert result is None or isinstance(result, Path)
