"""六行断言门禁（审查报告 §8）——给每条"声明"配一条能证伪它的断言。

检查项（任一失败 → 退出码 1，fail-closed）：
1. 两仓 `.gitattributes` 规则命中数 > 0          —— 字节稳定性声明真的存在
2. DEFAULT_DOCS 每一项都存在                     —— CLI 文档门禁覆盖范围不缩水
3. SCANNER 路径存在                              —— 发布前扫描器真的在（S41）
4. SBOM 条目数 == pip freeze 条目数              —— SBOM 不是一张直接依赖小表（S40）
5. `.sig` 工作区字节 == git blob 字节             —— 签名文件没有工作区漂移
6. `locale/*.po` 非中文 msgstr 比例 > 阈值        —— en_US 语言包不是半成品（S42）

用法：
  python tools/check_guardrails.py [--sbom <cdx.json>] [--po-threshold 0.95]

`--sbom` 缺省时读仓库根 `SBOM.json`；CI 里可在生成后立即传入同一路径，
保证比对的是同一环境产物。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MARKET_ROOT = REPO_ROOT.parent / "OmniCrawler-market"
PO_THRESHOLD_DEFAULT = 0.95
_PO_MSGSTR_CJK = re.compile(r'^msgstr ".*[\u4e00-\u9fff]')


def _git_blob(repo: Path, path: Path) -> bytes:
    """取指定路径在 git 索引（HEAD）中的字节。"""
    rel = path.relative_to(repo).as_posix()
    result = subprocess.run(
        ["git", "-C", str(repo), "show", f"HEAD:{rel}"],
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git show HEAD:{rel} 失败: {result.stderr.decode(errors='replace')[:200]}")
    return result.stdout


def check_gitattributes() -> list[str]:
    issues: list[str] = []
    for label, root in (("主仓", REPO_ROOT), ("市场仓", MARKET_ROOT)):
        path = root / ".gitattributes"
        if not path.is_file():
            issues.append(f"[1] {label} 缺少 .gitattributes")
            continue
        rules = [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if not rules:
            issues.append(f"[1] {label} .gitattributes 没有任何生效规则")
    return issues


def check_docs() -> list[str]:
    """DEFAULT_DOCS 每一项都必须存在（S44 修复后的同款约束）。"""
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    try:
        from check_cli_docs import DEFAULT_DOCS  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        return [f"[2] 无法导入 check_cli_docs.DEFAULT_DOCS: {exc}"]
    missing = [name for name in DEFAULT_DOCS if not (REPO_ROOT / name).is_file()]
    return [f"[2] DEFAULT_DOCS 缺失: {name}" for name in missing]


def check_scanner() -> list[str]:
    scanner = MARKET_ROOT / "tools" / "scan_plugin.py"
    if not scanner.is_file():
        return [f"[3] 发布前扫描器不存在: {scanner}"]
    return []


def check_sbom(sbom_path: Path | None) -> list[str]:
    path = sbom_path or (REPO_ROOT / "SBOM.json")
    if not path.is_file():
        return [f"[4] SBOM 不存在: {path}"]
    try:
        sbom = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"[4] SBOM 无法解析: {path}（{exc}）"]
    components = sbom.get("components", [])
    if not components:
        return ["[4] SBOM components 为空"]
    bad = [
        comp.get("name", "?")
        for comp in components
        if "not-installed" in str(comp.get("version", ""))
    ]
    if bad:
        return [f"[4] SBOM 含非法版本号（{len(bad)} 个）: {bad[:5]}..."]
    # 与同一环境 pip freeze 比对：SBOM 必须覆盖 freeze 的每个包
    freeze = subprocess.run(
        [sys.executable, "-m", "pip", "freeze"], capture_output=True, text=True
    ).stdout
    frozen = {
        line.split("==", 1)[0].lower().replace("_", "-")
        for line in freeze.splitlines()
        if "==" in line
    }
    sbom_names = {comp["name"].lower().replace("_", "-") for comp in components}
    missing = sorted(frozen - sbom_names)
    if missing:
        return [
            f"[4] SBOM 未覆盖 pip freeze 中的 {len(missing)} 个包（SBOM {len(sbom_names)} 条 vs freeze {len(frozen)} 条）: {missing[:8]}..."
        ]
    return []


def check_sig_bytes() -> list[str]:
    """市场仓已发布插件的签名文件：工作区字节必须等于 git 索引字节。"""
    sig = MARKET_ROOT / "plugins" / "example_news" / "plugin.py.sig"
    if not sig.is_file():
        return ["[5] 市场插件签名文件缺失: " + str(sig)]
    working = sig.read_bytes()
    try:
        blob = _git_blob(MARKET_ROOT, sig)
    except RuntimeError as exc:
        return [f"[5] {exc}"]
    if working != blob:
        return ["[5] 签名文件工作区字节与 git 索引不一致（存在未提交漂移）: " + str(sig)]
    return []


def check_po(threshold: float) -> list[str]:
    po_files = list((REPO_ROOT / "locale").rglob("*.po"))
    if not po_files:
        return ["[6] 未找到任何 .po 文件"]
    issues: list[str] = []
    for po in po_files:
        lines = po.read_text(encoding="utf-8").splitlines()
        total = sum(1 for line in lines if line.startswith("msgstr "))
        chinese = sum(1 for line in lines if _PO_MSGSTR_CJK.match(line))
        if total == 0:
            continue
        ratio = 1.0 - chinese / total
        if ratio < threshold:
            issues.append(
                f"[6] {po.relative_to(REPO_ROOT)} 中文 msgstr 比例 "
                f"{(1 - ratio):.1%}（非中文 {ratio:.1%} < {threshold:.0%}）"
            )
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="六行断言门禁（审查报告 §8）")
    parser.add_argument("--sbom", default=None, type=Path, help="SBOM 路径（默认仓库根 SBOM.json）")
    parser.add_argument("--po-threshold", default=PO_THRESHOLD_DEFAULT, type=float)
    args = parser.parse_args()

    checks = [
        check_gitattributes(),
        check_docs(),
        check_scanner(),
        check_sbom(args.sbom),
        check_sig_bytes(),
        check_po(args.po_threshold),
    ]
    issues = [item for group in checks for item in group]
    for issue in issues:
        print(f"FAIL {issue}")
    if issues:
        print(f"\n{len(issues)} 项门禁未通过。")
        return 1
    print("OK 六行断言门禁全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
