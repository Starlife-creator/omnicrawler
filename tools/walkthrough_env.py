"""One-command isolated environment for the manual walkthrough.

Why this exists: a walkthrough is only comparable across rounds if (a) the input
is fixed and (b) it does not touch your real data. This script gives both —
it serves the fixed demo site (`tools/walkthrough_demo_site.py`) and prints a
copy-paste block that isolates the application data root for this round only.

Usage::

    python tools/walkthrough_env.py                 # 工作区默认 .walkthrough/<时间戳>
    python tools/walkthrough_env.py --workspace D:/wt/2026-09-13

It prints the seed URL, the expected numbers (from the demo site's own
``EXPECTED`` — one source of truth), the launch commands, and where results land.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from walkthrough_demo_site import ATTACHMENT_PATH, EXPECTED, build_server

REPO_ROOT = Path(__file__).resolve().parents[1]


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
    server = build_server(args.port)
    port = server.server_port

    print("── 走查环境 ─────────────────────────────────────────")
    print(f"工作区      : {workspace}")
    print(f"演示站点    : http://127.0.0.1:{port}/")
    print(f"附件        : http://127.0.0.1:{port}{ATTACHMENT_PATH}")
    print(f"期望值      : 列表第 1 页 {EXPECTED['list_items']} 条；"
          f"翻页后合计 {EXPECTED['all_items']} 条；附件 {EXPECTED['attachments']} 个")
    print()
    print("── 启动 GUI（复制其中一段）──────────────────────────")
    print("PowerShell:")
    print(f'  $env:LOCALAPPDATA = "{data_root}"; cd "{project}"; python -m omnicrawler.gui')
    print("bash:")
    print(f'  LOCALAPPDATA="{data_root.as_posix()}" python -m omnicrawler.gui')
    print()
    app_data = data_root / "OmniCrawler"
    print(f"设置 LOCALAPPDATA 是为了让应用数据根落到 {app_data}，")
    print("不碰你真实的数据目录。")
    print()
    print("★ 项目根注意：GUI 用「设置里记住的项目目录」；自动探测是从 cwd 向上找含")
    print("  omnicrawler 的 pyproject.toml —— 在仓库里启动会指向仓库本身。")
    print(f"  请在 GUI 的项目选择器里指向 {project}，并在走查记录里写明实际项目根")
    print("  （配置与结果会落在 <项目根>/ 下）。")
    print()
    print("── 走查完成后 ───────────────────────────────────────")
    print("1. 结论填《审查记录》§二十（步骤见 docs/MANUAL_WALKTHROUGH.md）")
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
