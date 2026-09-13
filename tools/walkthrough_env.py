"""One-command isolated environment for the manual walkthrough.

Why this exists: a walkthrough is only comparable across rounds if (a) the input is
fixed and (b) it does not touch your real data. This script gives both, and it is
**cross-machine friendly** (see `docs/CAPABILITY_MATRIX.md`):

* **不假设平台** —— 隔离变量按平台给（Windows `LOCALAPPDATA`；POSIX `HOME`，依据
  `core/runtime_paths.portable_data_root()` 的回落顺序：`$LOCALAPPDATA/OmniCrawler`
  否则 `~/.omnicrawler`）；
* **不假设装了可选依赖** —— 缺 `reportlab`/`tesseract`/中文字体时**降级并给出补齐命令**，
  不是崩掉、也不是假装能用；
* 能力清单集中在 `capabilities()`，脚本输出与测试共用一处。

Usage::

    python tools/walkthrough_env.py                 # 工作区默认 .walkthrough/<时间戳>
    python tools/walkthrough_env.py --workspace D:/wt/round-1
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from walkthrough_demo_site import (
    ATTACHMENT_PATH,
    EXPECTED,
    attachment_available,
    attachment_skip_reason,
    build_server,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

#: 允许用环境变量指定本机的中文字体（探测器猜不到时的人工兜底）
CJK_FONT_ENV = "WALKTHROUGH_CJK_FONT"

#: 各平台常见的中文字体路径（探测顺序即优先级）
_CJK_FONT_CANDIDATES: dict[str, tuple[str, ...]] = {
    "win32": (
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simsun.ttc",
        "C:/Windows/Fonts/simhei.ttf",
    ),
    "darwin": (
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
    ),
    "linux": (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ),
}

_TESSERACT_FIX: dict[str, str] = {
    "win32": "Windows：一键脚本 setup_windows.bat 会准备项目内运行时；或安装 Tesseract 并设 TESSERACT_CMD",
    "darwin": "macOS：brew install tesseract tesseract-lang（或跑 setup_macos.command）",
    "linux": "Linux：sudo apt-get install -y tesseract-ocr tesseract-ocr-chi-sim（setup_linux.sh 已自动处理 apt/dnf）",
}

_CJK_FONT_FIX: dict[str, str] = {
    "win32": "Windows：系统自带微软雅黑，通常无需处理",
    "darwin": "macOS：系统自带苹方，通常无需处理",
    "linux": "Linux：sudo apt-get install -y fonts-noto-cjk（或 fonts-wqy-zenhei）",
}

#: 依赖分层提示（真源在 `docs/INSTALLATION.md` 与 `docs/SUPPORT_MATRIX.md`，此处只做指路）
_LAYER_HINT = (
    "最小层（只跑单元测试）：pip install -e \".[dev]\"\n"
    "推荐层（一键脚本，含浏览器与 tesseract）：见 docs/INSTALLATION.md\n"
    "按需层（PDF 附件 / GUI 走查）：pip install -e \".[dev,pdf,gui,html]\""
)


def _platform_key() -> str:
    """归一成 ``win32`` / ``darwin`` / ``linux``（其它平台归到 ``linux`` 的候选集）。"""
    if sys.platform == "win32":
        return "win32"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


def find_cjk_font(platform_key: str | None = None) -> Path | None:
    """探测本机可用的中文字体；环境变量 ``WALKTHROUGH_CJK_FONT`` 优先。"""
    override = os.environ.get(CJK_FONT_ENV, "").strip()
    if override:
        candidate = Path(override).expanduser()
        return candidate if candidate.is_file() else None
    key = platform_key or _platform_key()
    for raw in _CJK_FONT_CANDIDATES.get(key, ()):  # 平台候选
        candidate = Path(raw)
        if candidate.is_file():
            return candidate
    return None


def find_tesseract() -> Path | None:
    """探测 OCR 可执行：环境变量 → PATH → 仓库内 ``.runtime/tesseract/``。

    先调用产品自己的 `configure_runtime_environment()`，让"项目内置运行时"这条路径
    与产品行为一致（它会在存在时设置 `TESSERACT_CMD`）。
    """
    try:  # 与产品同源：让 .runtime/ 下的内置 tesseract 也能被发现
        from omnicrawler.core.runtime_paths import configure_runtime_environment

        configure_runtime_environment()
    except Exception:  # noqa: BLE001 —— 探测失败不影响其它能力
        pass

    for source in (
        os.environ.get("TESSERACT_CMD", "").strip(),
        shutil.which("tesseract") or "",
        str(REPO_ROOT / ".runtime" / "tesseract" / ("tesseract.exe" if _platform_key() == "win32" else "tesseract")),
    ):
        if source and Path(source).is_file():
            return Path(source)
    return None


def launch_commands(
    project: Path,
    data_root: Path,
    *,
    platform_key: str,
) -> dict[str, str]:
    """按平台给出隔离启动命令（**纯函数**，便于在单机上测另一平台）。

    隔离原理取自 `core/runtime_paths.portable_data_root()` 的实际回落顺序：
    `$LOCALAPPDATA/OmniCrawler`，变量不存在时用 `~/.omnicrawler`。因此：
    Windows 覆盖 `LOCALAPPDATA`，POSIX 覆盖 `HOME`（`Path.home()` 依赖它）。
    """
    if platform_key == "win32":
        return {
            "PowerShell": (
                f'$env:LOCALAPPDATA = "{data_root}"; cd "{project}"; python -m omnicrawler.gui'
            ),
            "bash（Windows 上的 Git Bash 等）": (
                f'cd "{project.as_posix()}"'
                f' && LOCALAPPDATA="{data_root.as_posix()}" python -m omnicrawler.gui'
            ),
        }
    # ★ 变量必须作用在 **python** 这一次调用上（`VAR=... cmd` 只对该 cmd 生效）：
    #   写成 `HOME="..." cd "..." && python ...` 时 HOME 只作用于 cd，
    #   python 仍会用真实 HOME ⇒ 隔离静默失效（本文件第一版就踩了这个坑）。
    return {
        "bash / zsh": (
            f'cd "{project.as_posix()}"'
            f' && HOME="{data_root.as_posix()}" python -m omnicrawler.gui'
        ),
    }


def capabilities(platform_key: str | None = None) -> list[dict[str, str]]:
    """走查所需能力的可用性清单（脚本输出与测试共用一处真值）。"""
    key = platform_key or _platform_key()
    font = find_cjk_font(key)
    tesseract = find_tesseract()
    return [
        {
            "name": "演示站点（列表/详情/翻页）",
            "ok": "yes",
            "detail": "只依赖标准库",
            "fix": "",
        },
        {
            "name": "PDF 附件（走查第 7 步）",
            "ok": "yes" if attachment_available() else "no",
            "detail": "reportlab 可用" if attachment_available() else attachment_skip_reason(),
            "fix": "" if attachment_available() else 'pip install -e ".[pdf]"',
        },
        {
            "name": "OCR（附件/扫描件链路）",
            "ok": "yes" if tesseract else "no",
            "detail": str(tesseract) if tesseract else "未找到 tesseract",
            "fix": "" if tesseract else _TESSERACT_FIX.get(key, "安装 tesseract 并设置 TESSERACT_CMD"),
        },
        {
            "name": "中文字体（界面渲染）",
            "ok": "yes" if font else "no",
            "detail": str(font) if font else f"未探测到中文字体（可用 {CJK_FONT_ENV} 指定）",
            "fix": "" if font else _CJK_FONT_FIX.get(key, "安装任一中文字体"),
        },
    ]


def prepare_workspace(workspace: Path) -> tuple[Path, Path]:
    """建干净工作区，返回 ``(项目根候选, 隔离数据根)``。"""
    project = workspace / "project"
    data_root = workspace / "data"
    project.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    return project, data_root


def main() -> None:
    parser = argparse.ArgumentParser(description="人工走查的隔离环境")
    parser.add_argument(
        "--workspace",
        type=Path,
        default=REPO_ROOT / ".walkthrough" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        help="本次走查的独立工作区（默认仓库内 .walkthrough/<时间戳>）",
    )
    parser.add_argument("--port", type=int, default=0, help="演示站点端口（0=随机）")
    args = parser.parse_args()

    workspace = args.workspace.resolve()
    project, data_root = prepare_workspace(workspace)
    key = _platform_key()
    server = build_server(args.port)
    port = server.server_port

    print("── 走查环境 ─────────────────────────────────────────")
    print(f"平台        : {key}")
    print(f"工作区      : {workspace}")
    print(f"演示站点    : http://127.0.0.1:{port}/")
    print(f"期望值      : 列表第 1 页 {EXPECTED['list_items']} 条；"
          f"翻页后合计 {EXPECTED['all_items']} 条")
    if attachment_available():
        print(f"附件        : http://127.0.0.1:{port}{ATTACHMENT_PATH}")
    print()
    print("── 能力清单（缺哪项就按 fix 补，不补也能走其余步骤）──")
    for item in capabilities(key):
        mark = "OK  " if item["ok"] == "yes" else "缺  "
        print(f"  [{mark}] {item['name']}：{item['detail']}")
        if item["fix"]:
            print(f"         补齐：{item['fix']}")
    print()
    print("── 依赖分层（按需选择，不必一次装全）──────────────────")
    for line in _LAYER_HINT.splitlines():
        print(f"  {line}")
    print()
    print("── 启动 GUI（复制一段；已做数据隔离）──────────────────")
    for shell, command in launch_commands(project, data_root, platform_key=key).items():
        print(f"{shell}:")
        print(f"  {command}")
    print()
    print("★ 隔离原理：数据根回落到 `$LOCALAPPDATA/OmniCrawler`，变量不存在时用 "
          "`~/.omnicrawler`；")
    print("  所以 Windows 覆盖 LOCALAPPDATA、POSIX 覆盖 HOME —— 都在本次工作区内。")
    print("★ 项目根：GUI 用设置里记住的目录；自动探测是从工作目录向上找含 omnicrawler 的")
    print("  pyproject.toml（在仓库里启动会指向仓库本身）。请在项目选择器里指向")
    print(f"  {project}，并在记录里写明实际项目根。")
    print()
    print("── 走查完成后 ───────────────────────────────────────")
    print("1. 结论填《审查记录》§二十（步骤与记录表见 docs/MANUAL_WALKTHROUGH.md）")
    print(f"2. 工作区可整目录删除：{workspace}")
    print()
    print("Ctrl+C 结束演示站点")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
