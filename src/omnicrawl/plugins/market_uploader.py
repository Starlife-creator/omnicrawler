"""市场上传器：通过 GitHub CLI (gh) 向市场仓库提交插件/模板 PR。

流程（用户视角）：
  1. 检查 gh 已安装且已登录（gh auth status）；
  2. fork 市场仓库（已 fork 则复用）；
  3. 在临时目录 clone fork → 创建分支 → 写入文件集；
  4. git commit + push → gh pr create（--repo 目标仓库 --head fork:分支）。

PR 内容为审核材料（含 creator 签名），不含维护者签名——CI 的
签名校验在维护者补 ``plugin.py.sig`` 后才会通过（预期流程）。

不依赖市场仓库与主仓库同目录（独立仓库：repo 参数显式指定）。
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

DEFAULT_MARKET_REPO = "Starlife-creator/OmniCrawler-market"


class UploadError(RuntimeError):
    """上传流程错误（fail-closed，消息面向用户）。"""


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    timeout: float = 300.0,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise UploadError(f"找不到可执行文件 {command[0]}（请安装并加入 PATH）") from exc
    except subprocess.TimeoutExpired as exc:
        raise UploadError(f"命令超时: {' '.join(command)}") from exc
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-400:]
        raise UploadError(f"命令失败（{command[0]}）：{detail}")
    return result


def _gh_username() -> str:
    result = _run(["gh", "api", "user", "--jq", ".login"])
    return result.stdout.strip()


def ensure_gh() -> str:
    """确认 gh 可用并已登录，返回 GitHub 用户名（否则抛 UploadError）。"""
    _run(["gh", "--version"])
    _run(["gh", "auth", "status"], check=False)
    status = _run(["gh", "auth", "status"])
    if "logged in" not in status.stdout.lower() and "已登录" not in status.stdout:
        raise UploadError("gh 未登录。请先运行: gh auth login")
    return _gh_username()


def _ensure_fork(target_repo: str, me: str) -> str:
    """确保 <me>/<target_repo 短名> 存在（fork 或复用）。"""
    short = target_repo.split("/")[1]
    fork_repo = f"{me}/{short}"
    probe = _run(["gh", "repo", "view", fork_repo], check=False)
    if probe.returncode == 0:
        return fork_repo
    _run(["gh", "repo", "fork", target_repo, "--clone=false"])
    return fork_repo


def create_market_pr(
    *,
    files: dict[str, bytes],
    title: str,
    body: str,
    target_repo: str = DEFAULT_MARKET_REPO,
) -> str:
    """把文件集提交到市场仓库并创建 PR，返回 PR URL。

    ``files``：仓库相对路径 -> 内容（见 plugin_packaging.build_*_upload）。
    """
    if not files:
        raise UploadError("上传文件集为空")
    for rel in files:
        if ".." in rel.split("/") or rel.startswith("/"):
            raise UploadError(f"非法文件路径: {rel}")

    me = ensure_gh()
    fork_repo = _ensure_fork(target_repo, me)

    branch = f"submit/{next(iter(files)).split('/')[0]}-{_branch_suffix()}"

    with tempfile.TemporaryDirectory(prefix="omnicrawler-market-") as temp:
        work = Path(temp) / "market"
        _run(["gh", "repo", "clone", fork_repo, str(work)])
        _run(["git", "config", "user.email", f"{me}@users.noreply.github.com"], cwd=work)
        _run(["git", "config", "user.name", me], cwd=work)
        _run(["git", "checkout", "-b", branch], cwd=work)
        for rel, content in files.items():
            target = work / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        _run(["git", "add", "-A"], cwd=work)
        _run(["git", "commit", "-m", title], cwd=work)
        _run(["git", "push", "origin", branch], cwd=work)
        pr = _run(
            [
                "gh",
                "pr",
                "create",
                "--repo",
                target_repo,
                "--head",
                f"{me}:{branch}",
                "--title",
                title,
                "--body",
                body,
            ],
            cwd=work,
        )
    return pr.stdout.strip() or f"https://github.com/{target_repo}/pulls"


def _branch_suffix() -> str:
    import time

    return time.strftime("%Y%m%d-%H%M%S")


def pr_body(payload_kind: str, plugin_id: str, username: str) -> str:
    """标准 PR 描述（含维护者签名提示，按 payload 类型分支）。"""
    if payload_kind == "template":
        sign_hint = (
            "python tools/sign_plugin.py sign templates/<id>/template.yaml "
            "--private-key <冷存储信任根私钥>"
        )
    else:
        sign_hint = (
            "python tools/sign_plugin.py sign plugins/<id>/plugin.py "
            "--private-key <冷存储信任根私钥>"
        )
    return (
        f"提交者：{username}\n"
        f"类型：{payload_kind}\n"
        f"ID：{plugin_id}\n\n"
        "本 PR 仅含创作者签名（审核材料）。审核通过后请维护者执行（用冷存储信任根私钥"
        "覆盖 <file>.sig，下载端/CI 校验此文件；不要用 maintainer-sign，其产出 "
        "maintainer.sig 下载端不校验）：\n"
        "```\n"
        f"{sign_hint}\n"
        "```\n"
        "补充维护者签名后 CI 签名校验通过，再合并并更新 catalog.json。\n"
    )
