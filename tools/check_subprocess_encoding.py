"""静态门禁：禁止「按 UTF-8 解子进程输出、却不给子进程 UTF-8 环境」的新位点。

## 为什么需要这条守卫

父进程写 `subprocess.run(..., encoding="utf-8")` 时，**子进程必须按 UTF-8 写**。
Windows 上 Python 子进程默认按控制台代码页（简体中文 cp936/GBK）写管道，中文会被父进程
解成一串 U+FFFD —— 于是「代码做对了，断言却判红」。2026-11-14 两次踩到同一类：
`tools/self_verify.py` 与 `tests/unit/tools/test_update_manifest_tools.py`。

修已知的红是还债；**防止新增才是资产**。这条门禁扫 git 已跟踪的 `.py`，找出
「发起子进程 + 显式声明 `encoding=` + 没有 `env=`」的调用，逐个要求：

- 要么带上 `env=`（推荐 `from tools.child_env import utf8_env`）；
- 要么写进 :data:`ALLOWED`，并给出**为什么这里不需要**的一句话理由。

## 为什么必须按 AST 判，不能按正则

`path.read_text(encoding="utf-8")`、`open(..., encoding=...)`、日志配置里的 `encoding=`
与子进程毫无关系；正则会把它们一网打尽，然后为了放行而不断加白，最后守卫变成摆设。
AST 只认「`subprocess.<发起子进程的函数>(...)` 这个调用的关键字参数」。

## 为什么白名单要带理由

白名单本身是一种「悄悄放行」。要求每条都写理由，是为了让后来者能**复核那个理由**，
而不是看到一个名字就绕过去。理由文案不参与判定，但空字符串会被判红。

## 反向断言（都在 tests/unit/tools/test_subprocess_encoding_gate.py）

1. 新塞一个违规位点 ⇒ 必红（`new-violation`）；
2. 把它改回带 `env=` ⇒ 必绿；
3. 扫描面收窄（只扫一个目录 / 扫不到文件）⇒ 必红（`empty-selection`）；
4. 白名单里出现空理由 ⇒ 必红（`blank-justification`）；
5. 白名单里出现仓库中已不存在的位点 ⇒ 必红（`stale-allowance`），
   防止「改好了但白名单还留着」把守卫悄悄撑大。
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: 发起子进程的调用名（只取最后一段：`subprocess.run` → `run`）。
SPAWNERS: frozenset[str] = frozenset(
    {
        "run",
        "call",
        "check_call",
        "check_output",
        "Popen",
        "getoutput",
        "getstatusoutput",
    }
)

#: 允许「声明 encoding 却不给 env」的位点 → 理由（必须非空，供人复核）。
#: 键格式 `相对路径:行号`；行号对不上会在扫描时被判 `stale-allowance`。
ALLOWED: dict[str, str] = {
    # ---- git / bash 这类**外部程序一律按 UTF-8 写**，与 Python 的控制台代码页无关 ----
    "tools/bump_version.py:265": "子进程是 git log；git 输出固定 UTF-8，源码注释亦已注明。",
    "tools/bump_version.py:289": "子进程是 git rev-parse；git 输出固定 UTF-8。",
    "tools/bump_version.py:296": "子进程是 git tag；git 输出固定 UTF-8。",
    "tools/check_gitignore_hygiene.py:136": "子进程是 git；输出固定 UTF-8。",
    "tools/check_shell_scripts.py:126": "子进程是 Git bash；MSYS bash 的 err 输出按 UTF-8 写。",
    "tools/check_shell_scripts.py:146": "子进程是 Git bash -n；同上。",
    "tools/check_shell_scripts.py:168": "子进程是 Git bash -n；同上。",
    "tools/check_shell_scripts.py:186": "子进程是 Git bash -n；同上。",
    "tests/unit/tools/test_linux_install_scripts.py:565": "子进程是 git check-attr；输出固定 UTF-8。",
    "tests/unit/tools/test_sync_snapshot_pin.py:27": "子进程是 git；输出固定 UTF-8。",
    "tests/unit/utils/test_checkout_market.py:15": "子进程是 git；输出固定 UTF-8。",
    # ---- 被探测方自己把 stdout 重配成 UTF-8，父按 utf-8 解是对的 ----
    "src/omnicrawler/gui/runner/env_checker.py:64": (
        "子进程是 OmniCrawler CLI（--version）；src/omnicrawler/__main__.py:183 已 reconfigure(utf-8)。"
    ),
    "src/omnicrawler/services/replay.py:227": (
        "子进程是内置 _REPLAY_SCRIPT；replay.py:46 已 stdout.reconfigure(utf-8)。"
    ),
    "tests/integration/test_gui_worker_local_task.py:469": (
        "子进程是 python -m omnicrawler compare-runs；CLI 自 reconfigure(utf-8)，且断言只看退出码与产出文件。"
    ),
    "tests/unit/pdf/test_pdfx_config_position.py:82": (
        "子进程是 python -m omnicrawler pdf；CLI 自 reconfigure(utf-8)，中文断言因此可靠。"
    ),
    # ---- 子进程输出不参与中文断言（只比 ASCII 标记）/ 不参与判定 ----
    "tests/unit/tools/test_portable_archive_smoke.py:265": (
        "只断言 --help 里的 ASCII 选项名；即使中文乱码也不影响判定。"
    ),
    "src/omnicrawler/plugins/plugin_audit.py:271": (
        "只匹配 ASCII 的 '\"ok\": true'；中文不参与判定。"
    ),
    "src/omnicrawler/plugins/market_uploader.py:36": (
        "子进程是 git/gh/curl 等外部工具，输出固定 UTF-8。"
    ),
    "tools/ressign_market.py:212": (
        "子进程输出只原样转发给控制台、不参与任何判定；最坏是显示乱码。"
    ),
    # ---- 已修复的位点：保留在此是为了让「改好之后还留着白」这件事被看见，
    #      而不是让它们继续算违规。等确认稳定后可整条删除（删了由 scan 判 stale 提醒）。----
}

_SKIP_PARTS = frozenset(
    {".git", ".venv", ".tmp", ".audit-tmp", ".agents", ".test-tmp", "node_modules", "__pycache__", ".runtime"}
)


@dataclass(frozen=True, slots=True)
class Site:
    """一处「发起子进程且声明了 encoding」的调用点。"""

    path: str
    line: int
    spawner: str
    encoding: str
    has_env: bool

    @property
    def key(self) -> str:
        return f"{self.path}:{self.line}"

    def describe(self) -> str:
        return (
            f"{self.key}: subprocess.{self.spawner}(encoding={self.encoding}) 未传 env= —— "
            "父进程按 UTF-8 解子进程输出，就必须给子进程 UTF-8 环境"
            "（Windows 默认控制台下 Python 子进程按 cp936 写，中文会变成 U+FFFD）。"
            f"修法：`from tools.child_env import utf8_env` 后加 `env=utf8_env()`；"
            f"若这里确实不需要，写进 tools/check_subprocess_encoding.py 的 ALLOWED 并给出理由。"
        )


@dataclass(frozen=True, slots=True)
class Finding:
    rule: str
    detail: str

    def describe(self) -> str:
        return f"[{self.rule}] {self.detail}"


def _attr_name(node: ast.AST) -> str:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def tracked_python_files(root: Path) -> list[str]:
    """返回 git 已跟踪的 .py（相对路径）。不在 git 仓里则退回文件系统遍历。"""
    try:
        done = subprocess.run(
            ["git", "ls-files", "-z", "--", "*.py"],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**_utf8(), "GIT_OPTIONAL_LOCKS": "0"},
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        done = None
    if done is not None and done.returncode == 0 and done.stdout.strip():
        return sorted({p for p in done.stdout.split("\0") if p})
    return sorted(
        str(path.relative_to(root)).replace("\\", "/")
        for path in root.rglob("*.py")
        if not (_SKIP_PARTS & set(path.relative_to(root).parts))
    )


def _utf8() -> dict[str, str]:
    import os

    return {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


def scan_file(root: Path, relative: str) -> list[Site]:
    path = root / relative
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return []
    sites: list[Site] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        spawner = _attr_name(node.func)
        if spawner not in SPAWNERS:
            continue
        kwargs = {kw.arg: kw for kw in node.keywords if kw.arg}
        if "encoding" not in kwargs:
            continue
        value = kwargs["encoding"].value
        encoding = repr(value.value) if isinstance(value, ast.Constant) else "<expr>"
        sites.append(
            Site(
                path=relative,
                line=node.lineno,
                spawner=spawner,
                encoding=encoding,
                has_env="env" in kwargs,
            )
        )
    return sites


def scan(root: Path = PROJECT_ROOT) -> tuple[list[Site], list[Finding]]:
    """返回（全部位点, 问题清单）。位点表用于反向断言里核对扫描面。"""
    files = tracked_python_files(root)
    sites: list[Site] = []
    for relative in files:
        sites.extend(scan_file(root, relative))

    findings: list[Finding] = []
    if not files:
        findings.append(Finding("empty-selection", "选中的 .py 为 0 个 —— 扫描面失效，空集不是通过"))
    if not sites:
        findings.append(
            Finding("empty-selection", "一个「发起子进程且声明 encoding」的调用都没找到 —— 判据失效")
        )

    offenders = [site for site in sites if not site.has_env]
    keys = {site.key for site in sites}

    for site in offenders:
        if site.key in ALLOWED:
            continue
        findings.append(Finding("unsafe-subprocess-encoding", site.describe()))

    for key, reason in ALLOWED.items():
        if not reason.strip():
            findings.append(Finding("blank-justification", f"白名单位点 {key} 没有写理由"))
        if key not in keys:
            findings.append(
                Finding(
                    "stale-allowance",
                    f"白名单位点 {key} 已不存在（改好或行号变了）⇒ 请删掉这条豁免，别让守卫被悄悄撑大",
                )
            )
    return sites, findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检查子进程输出的编码约定")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = parser.parse_args(argv)

    sites, findings = scan(args.root)
    offenders = [site for site in sites if not site.has_env]

    if args.json:
        payload = {
            "ok": not findings,
            "sites": len(sites),
            "sites_without_env": len(offenders),
            "allowlisted": len(ALLOWED),
            "findings": [{"rule": f.rule, "detail": f.detail} for f in findings],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if not findings else 1

    if findings:
        print("子进程编码约定检查失败：", file=sys.stderr)
        for finding in findings:
            print(f"  - {finding.describe()}", file=sys.stderr)
        return 1

    print(
        f"子进程编码约定检查通过（{len(sites)} 处发起子进程且声明 encoding 的调用，"
        f"{len(offenders)} 处未传 env= 且都有书面理由）。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
