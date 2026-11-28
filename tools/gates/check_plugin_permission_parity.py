#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""插件权限白名单：主仓与市场仓**必须同集合**。

## 为什么需要这道门（2026-11-27 实测）

插件权限有两份白名单：

| 位置 | 用途 |
|---|---|
| 主仓 `plugin_sandbox.ALLOWED_PERMISSIONS` | **加载时**判「这个插件能不能跑」 |
| 市场 `validate_submission.ALLOWED_PLUGIN_PERMISSIONS` | **投稿时**判「这个插件能不能发」 |

两边不同集合会造成**单向死锁**，而且症状都不指向根因：

```
主仓有、市场无  ⇒  插件本地能跑，但投稿被拒（unknown permissions）
                   作者以为是自己写错了，其实是白名单漏同步
市场有、主仓无  ⇒  投稿过审、用户装了之后加载失败
                   （审核放行了一个跑不起来的插件）
```

实测漏项：`component:ocr`（主仓 2026-11-17 加的，市场侧漏了三个月）。
后果是「任何用组件式 OCR 代理的插件都发不出去」，而
`docs/PLUGIN_CONTRACT.md` 早就把这条能力写进表里了。

## 判据

两个集合**逐项相等**（不是包含、不是交集）。任一侧多出条目都判红，
并指出多在哪一侧、多的是什么 —— 报错必须能直接指导修哪边。

## 边界（如实说明）

这**不**证明两边的「权限含义」一致（例如 `files:read` 在两边是否同样约束路径），
那只靠同一批 e2e 用例。它证明的是「白名单没有漏同步」，
能拦住上面那两类死锁。

市场仓不可达时**跳过**（本地开发常只 clone 主仓），但会打印原因 ——
静默跳过等于假装验过。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from omnicrawler.plugins.plugin_broker_contracts import CAPABILITY_VERSIONS  # noqa: E402
from omnicrawler.plugins.plugin_sandbox import ALLOWED_PERMISSIONS  # noqa: E402

MARKET_VALIDATOR = _ROOT.parent / "OmniCrawler-market" / "tools" / "validate_submission.py"

_PERMISSION = re.compile(r"\"([a-z][a-z0-9_]*:[a-z][a-z0-9_]*)\"")
_CAPABILITY = re.compile(r"\"([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+)\"")


def _strip_comments(block: str) -> str:
    """去掉 `#` 起的注释（逐行，行内注释也去掉）。

    ★ 必须做：真实文件里那段集合**带着注释**（「2026-11-27：运行期切换…」），
      而注释里可能出现像权限名的引号字符串。不剥注释会把它们计进集合，
      于是「市场少一项」的判据可能被一条注释抵消 —— 假绿。

      简洁做法：按行切，取 `#` 之前的部分。权限名本身不含 `#`，安全。
    """
    kept = []
    for line in block.splitlines():
        hash_index = line.find("#")
        kept.append(line if hash_index < 0 else line[:hash_index])
    return "\n".join(kept)


def market_permissions(source: str) -> set[str]:
    """从市场校验器源码里取出 `ALLOWED_PLUGIN_PERMISSIONS` 的集合（已剥注释）。"""
    match = re.search(r"ALLOWED_PLUGIN_PERMISSIONS = \{(.*?)\n\}", source, re.S)
    if match is None:
        return set()
    return set(_PERMISSION.findall(_strip_comments(match.group(1))))


def market_capabilities(source: str) -> set[str]:
    """从市场校验器源码里取出 `ALLOWED_CAPABILITIES` 的集合（已剥注释）。"""
    match = re.search(r"ALLOWED_CAPABILITIES = \{(.*?)\n\}", source, re.S)
    if match is None:
        return set()
    return set(_CAPABILITY.findall(_strip_comments(match.group(1))))


def differences(
    core: set[str], market: set[str]
) -> tuple[list[str], list[str]]:
    """返回 `(主仓多出, 市场多出)`，各自排序。"""
    return sorted(core - market), sorted(market - core)


def main(argv: list[str] | None = None) -> int:
    del argv
    if not MARKET_VALIDATOR.is_file():
        print(f"[SKIP] 没有市场 checkout：{MARKET_VALIDATOR}")
        return 0
    source = MARKET_VALIDATOR.read_text(encoding="utf-8")
    market = market_permissions(source)
    core = set(ALLOWED_PERMISSIONS)
    if not market:
        # ★ 解析失败不能当成「市场没有权限」—— 那会让下面的判据恒真。
        print("[FAIL] 无法从市场校验器里解析出 ALLOWED_PLUGIN_PERMISSIONS —— 判据失效")
        return 1
    if not core:
        print("[FAIL] 主仓 ALLOWED_PERMISSIONS 是空的 —— 空集合不是通过")
        return 1

    failures = 0

    only_core, only_market = differences(core, market)
    if only_core or only_market:
        failures += 1
        print(f"[FAIL] 插件权限白名单两侧不一致（主仓 {len(core)} / 市场 {len(market)}）：")
        for name in only_core:
            print(
                f"  - {name!r} 只在主仓 ⇒ 插件本地能跑，但投稿会被市场以"
                f" `unknown permissions` 拒收"
            )
        for name in only_market:
            print(
                f"  - {name!r} 只在市场 ⇒ 投稿能过审，但用户装了之后加载失败"
                "（审核放行了一个跑不起来的插件）"
            )
    else:
        print(f"[OK] 插件权限白名单两侧一致（各 {len(core)} 项）")

    # ★ 能力名是**同一类跨仓漂移**：市场 `ALLOWED_CAPABILITIES` 管投稿时的
    #   `required_capabilities` 校验，主仓 `CAPABILITY_VERSIONS` 管运行期。
    #   两侧不同集合 ⇒ 同样两种死锁（写得出但发不出 / 发得出但跑不起来）。
    capability_core = set(CAPABILITY_VERSIONS)
    capability_market = market_capabilities(source)
    if not capability_market:
        failures += 1
        print("[FAIL] 无法从市场校验器里解析出 ALLOWED_CAPABILITIES —— 判据失效")
    else:
        cap_only_core, cap_only_market = differences(capability_core, capability_market)
        if cap_only_core or cap_only_market:
            failures += 1
            print(
                f"[FAIL] 能力白名单两侧不一致（主仓 {len(capability_core)} / "
                f"市场 {len(capability_market)}）："
            )
            for name in cap_only_core:
                print(f"  - {name!r} 只在主仓 ⇒ 声明它的插件投稿会被拒")
            for name in cap_only_market:
                print(f"  - {name!r} 只在市场 ⇒ 声明它的插件过审后运行期被拒载")
        else:
            print(f"[OK] 宿主能力白名单两侧一致（各 {len(capability_core)} 项）")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
