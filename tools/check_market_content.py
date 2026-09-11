"""市场内容质量：把「审查清单」里**可机器判定**的部分变成证据。

## 背景：信任档位的输入一直没有生产者

`omnicrawler.plugins.plugin_router.classify_in_process_tier` 用 ``gates_evidence`` 决定信任档位：

    T1 = maintainer_signed ∧ gates_evidence ∧ 契约 2 ∧ 无高危能力

市场 catalog 的 schema 也把 ``review_depth`` / ``gates_evidence`` 列为合法字段
（见市场仓库 `tools/catalog_lib/common.py`），`docs/PLUGIN_REVIEW_CHECKLIST.md` 还要求核对它。
但**没有任何代码写过这两个字段**——六个已发布插件的 catalog 条目里它们全为空，
于是「证据齐全 → T1」这条通路**永远走不到**。这是本项目第 5 例「定义了没有生产者」。

## 本工具做什么

对市场 checkout 的每个插件执行**审查清单里可机器判定的部分**，产出 :func:`gates_evidence`，
并据此推导 ``review_depth``：

* 声明过的每个文件都存在（插件本体、签名、身份、包清单、说明文档）；
* ``package_manifest_sha256`` 与包清单**实际内容**一致（这条抓的是「清单改了但哈希没更新」）；
* 签名文件非空（真正的验签仍由维护者私钥流程负责，见 `docs/PLUGIN_REVIEW_CHECKLIST.md`）；
* 许可存在且在市场白名单内；
* ``README.md`` 与 ``tests/`` 存在（「容易理解和复用」的最低可判定形态）。

``--check``（默认）把计算结果与 catalog 声明比对，**声明与证据不一致即失败**；
``--json`` 导出证据，供市场侧的 catalog 生成流程填入（从而给信任档位一个真实输入）。

## 边界（如实说明）

本工具**不替代**人工审查：安全边界、界面质量、文档可读性等仍需人看。
它把「可由机器判定」的部分固化下来，使 ``review_depth`` 有据可依，而不是凭印象填。
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

#: 市场仓库在工作区里的默认位置（CI 用 checkout_market.py 拷到同级目录）。
DEFAULT_MARKET = REPO_ROOT.parent / "OmniCrawler-market"

#: 许可白名单（与 `docs/PLUGIN_REVIEW_CHECKLIST.md` 的门 2 一致）。
SPDX_ALLOWLIST = frozenset(
    {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "MPL-2.0", "0BSD", "Unlicense"}
)

#: 模块级导入这些**顶层包**意味着「导入期就可能触网」。
_ALWAYS_NETWORK = frozenset(
    {
        "socket", "ssl", "ftplib", "smtplib", "telnetlib", "paramiko",
        "requests", "httpx", "aiohttp", "websockets", "websocket",
    }
)

#: 只有这些**子模块**才做网络 I/O。
#:
#: 刻意精确到子模块：`urllib.parse` 只是字符串解析、`urllib.error` 只是异常类型，
#: 把它们一并算作「触网」会造成误报——实测两个市场插件就因此被误判（首版规则的教训）。
_NETWORK_SUBMODULES = frozenset(
    {"urllib.request", "http.client", "http.server", "xmlrpc.client", "asyncio.streams"}
)

#: 条目里声明的、必须真实存在的文件字段。
REQUIRED_FILE_FIELDS = (
    "plugin_file",
    "signature_file",
    "description_file",
    "creator_identity_file",
    "creator_signature_file",
    "package_manifest_file",
    "creator_package_signature_file",
    "maintainer_package_signature_file",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _is_network_module(name: str) -> bool:
    """该模块名是否会在导入期触网。"""
    if name.split(".")[0] in _ALWAYS_NETWORK:
        return True
    return any(name == module or name.startswith(module + ".") for module in _NETWORK_SUBMODULES)


def _module_level_network_imports(plugin_py: Path) -> list[str]:
    """返回 ``plugin.py`` **模块级**导入的网络库名。

    只看模块级：函数体内部按需导入不算「导入期触网」。
    """
    try:
        tree = ast.parse(plugin_py.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    found: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names if _is_network_module(alias.name))
        elif isinstance(node, ast.ImportFrom) and _is_network_module(node.module or ""):
            found.append(node.module or "")
    return found


def _declares_network(entry: dict[str, Any]) -> bool:
    """插件是否声明了网络能力（这类插件按定义「需要网络」，离线可用不适用）。"""
    permissions = entry.get("permissions") or []
    if any("network" in str(item) for item in permissions if isinstance(item, str)):
        return True
    return bool(entry.get("domains"))


def _check_plugin(entry: dict[str, Any], market: Path) -> dict[str, bool]:
    """对单个 catalog 条目执行可机器判定的检查；返回 检查名 → 是否通过。"""
    checks: dict[str, bool] = {}

    # 1) 声明的文件都存在
    for field in REQUIRED_FILE_FIELDS:
        declared = str(entry.get(field, "") or "")
        checks[f"file:{field}"] = bool(declared) and (market / declared).is_file()

    # 2) 包清单哈希与内容一致
    manifest_field = str(entry.get("package_manifest_file", "") or "")
    expected = str(entry.get("package_manifest_sha256", "") or "")
    manifest_path = market / manifest_field if manifest_field else None
    if manifest_path is not None and manifest_path.is_file() and expected:
        checks["package_manifest_sha256"] = _sha256(manifest_path) == expected
    else:
        checks["package_manifest_sha256"] = False

    # 3) 签名文件非空
    for field in ("signature_file", "creator_signature_file", "maintainer_package_signature_file"):
        declared = str(entry.get(field, "") or "")
        path = market / declared if declared else None
        checks[f"signature_nonempty:{field}"] = bool(
            path is not None and path.is_file() and path.stat().st_size > 0
        )

    # 4) 许可在白名单内
    license_id = str(entry.get("license", "") or "").strip()
    checks["license_allowlisted"] = license_id in SPDX_ALLOWLIST

    # 5) 可理解与可复用的最低可判定形态
    plugin_dir = market / "plugins" / str(entry.get("id", ""))
    checks["readme_present"] = (plugin_dir / "README.md").is_file()
    checks["tests_present"] = (plugin_dir / "tests").is_dir()

    # 6) 离线可用（仅对未声明网络能力的插件适用）
    #    规则：模块级不得导入网络库——否则「没有网络就装不上/跑不起来」。
    #    声明了网络能力的插件按定义需要网络，本项不适用（因此不进证据，避免恒真的假信号）。
    if not _declares_network(entry):
        plugin_py = market / str(entry.get("plugin_file", "") or "")
        checks["offline_import_safe"] = bool(plugin_py.is_file()) and not _module_level_network_imports(
            plugin_py
        )
    return checks


def gates_evidence(entry: dict[str, Any], market: Path) -> dict[str, Any]:
    """产出该条目的门禁证据（可直接写进 catalog 的 ``gates_evidence``）。"""
    checks = _check_plugin(entry, market)
    return {
        "checks": checks,
        "needs_network": _declares_network(entry),
        "passed": all(checks.values()),
        "failed_checks": sorted(name for name, ok in checks.items() if not ok),
        "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "gate": "tools/check_market_content.py",
    }


def derived_review_depth(evidence: dict[str, Any]) -> str:
    """由证据推导质量档：全部通过 → ``reviewed``，否则 ``signed_only``。"""
    return "reviewed" if evidence.get("passed") else "signed_only"


def audit_market(market: Path) -> dict[str, Any]:
    """审计整个 catalog，返回每条目的证据与「声明 vs 证据」的一致性问题。"""
    catalog_path = market / "catalog.json"
    if not catalog_path.is_file():
        raise FileNotFoundError(f"找不到 catalog: {catalog_path}")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))

    results: dict[str, Any] = {}
    issues: list[str] = []
    for entry in catalog.get("plugins", []):
        plugin_id = str(entry.get("id", "?"))
        evidence = gates_evidence(entry, market)
        derived = derived_review_depth(evidence)
        declared = entry.get("review_depth")
        results[plugin_id] = {"evidence": evidence, "derived_review_depth": derived, "declared": declared}

        if declared is None:
            # 未声明不算违规：改由市场侧按需填入（本工具提供的正是那个输入）。
            continue
        if declared != derived:
            issues.append(
                f"{plugin_id}: catalog 声明 review_depth={declared!r}，但机器证据推导为 {derived!r}"
                f"（未通过：{evidence['failed_checks']}）"
            )
        elif declared == "reviewed" and not entry.get("gates_evidence"):
            issues.append(f"{plugin_id}: 声明 reviewed 却没有随附 gates_evidence——该档位无据可依")
    return {"market": str(market), "plugins": results, "issues": issues}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验市场内容质量：把可机器判定的审查项变成证据")
    parser.add_argument("--market", default=str(DEFAULT_MARKET), help="市场 checkout 目录")
    parser.add_argument("--json", dest="json_path", help="把证据报告写入该 JSON 文件")
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="只产出证据、不因声明与证据不一致而失败（用于观察现状）",
    )
    args = parser.parse_args(argv)

    market = Path(args.market).expanduser()
    if not (market / "catalog.json").is_file():
        print(f"跳过：找不到市场 checkout 或 catalog（{market}）", file=sys.stderr)
        return 0

    report = audit_market(market)
    plugins = report["plugins"]
    reviewed = [name for name, item in plugins.items() if item["derived_review_depth"] == "reviewed"]

    print(f"市场内容质量：{len(plugins)} 个插件，机器证据支持 reviewed 的 {len(reviewed)} 个")
    for name, item in sorted(plugins.items()):
        failed = item["evidence"]["failed_checks"]
        mark = "通过" if not failed else f"未通过 {len(failed)} 项"
        print(f"  {name:<24} {item['derived_review_depth']:<14} {mark}")
        if failed:
            print(f"      未通过项：{', '.join(failed)}")

    if args.json_path:
        target = Path(args.json_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"证据报告：{target}")

    if report["issues"]:
        print(f"\n声明与证据不一致 {len(report['issues'])} 项：")
        for issue in report["issues"]:
            print(f"  - {issue}")
        if not args.report_only:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
