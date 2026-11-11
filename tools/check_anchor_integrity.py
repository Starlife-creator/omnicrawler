"""锚段完整性门禁：`优化方案.md` §一 / §1.1 / §1.2 / §1.3 被改动时必须留下可审的 diff。

## 为什么需要这个脚本

`优化方案.md` §1.3 规定「锚（最终目标 / 现实条件 / 最终效果）**不可擅动**」，
但这份文件在**项目根目录**，而主仓是 `OmniCrawler/` 子目录、根目录**不是 git 仓**：

* 没有版本控制 ⇒ 锚被改了不会有任何 diff；
* 没有任何 `tools/check_*.py` 按路径读过它（只有 4 处 docstring 提到「对应 §x.x」）；
* CI 的 checkout 只拿主仓 ⇒ 远端也看不见它。

结果是：**全项目唯一「不可擅动」的东西，恰恰是唯一零守卫的真源。**
这正是它已经静默漂移过的地方（版本头停在 0.15.0 而真值早已 1.0.1）。

## 选项① 的做法（本脚本）

主仓内保存锚段的**摘要**（`tools/anchor_digest.json`：段落 id + 归一化文本的 sha256），
本脚本按路径读取根级 `优化方案.md`、重新算摘要、与登记值比对：

* 段落标题找不到 ⇒ **红**（锚段被改名/删除，`anchor section not found`）；
* 摘要不一致 ⇒ **红**（锚段内容被改，`anchor section changed`）；
* 登记表为空 / 解析到 0 段 ⇒ **红**（「选中 0 段」不是通过）；
* 根级文件不存在 ⇒ 显式 **SKIP**（CI 拿不到根目录，这是已知且被记录的边界）；
  但若根目录出现了「疑似改名后的锚文件」⇒ **红**，避免改名后静默跳过。

因此：改锚不再是「一次无声编辑」，而必须**同时改摘要**——那个摘要变更就是
可审的 diff，且在根级非 git 仓的前提下，这是唯一能留下痕迹的位置。

## 用法

    python tools/check_anchor_integrity.py            # 门禁模式（只读）
    python tools/check_anchor_integrity.py --update   # 有意改锚后重算摘要（会写盘）
    python tools/check_anchor_integrity.py --json     # 机器可读

**边界（如实说明）**：摘要能证明「锚段变了」，不能证明「改得对不对」。
它把「静默改锚」降级为「必须显式改摘要 + 提交一次 diff」，判断仍要人来做。
CI 里本门禁必然 SKIP（根目录不在 checkout 内）——它守的是**维护者本机**。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DIGEST_PATH = REPO_ROOT / "tools" / "anchor_digest.json"

# 锚段定义：id → 起始标题（前缀匹配，标题尾部括号说明允许变化）。
# 顺序即文档顺序；段落边界由「下一个同级或更高级标题」决定。
ANCHOR_SECTIONS: tuple[tuple[str, str], ...] = (
    ("§一 最终目标", "## 一、最终目标"),
    ("§1.1 不变的现实条件", "### 1.1 不变的现实条件"),
    ("§1.2 最终应达到的产品效果", "### 1.2 最终应达到的产品效果"),
    ("§1.3 判据层级与原则治理", "### 1.3 判据层级与原则治理"),
)


def anchor_file(root: Path) -> Path:
    """锚文件位置：主仓的**上一级**（项目根）下的 `优化方案.md`。"""
    return root.parent / "优化方案.md"


def _heading_level(line: str) -> int:
    stripped = line.lstrip()
    if not stripped.startswith("#"):
        return 0
    hashes = len(stripped) - len(stripped.lstrip("#"))
    # `#######` 以上不是标题；`#` 后面必须是空白或行尾
    if hashes > 6 or (len(stripped) > hashes and not stripped[hashes].isspace()):
        return 0
    return hashes


def _normalise(lines: list[str]) -> str:
    """归一化：逐行去尾部空白，去掉尾部空行与 `---` 版式分隔线。

    行尾差异（CRLF/LF）与尾部版式**不算改锚**——否则门禁会被无关改动频繁打红，
    最终被人关掉。真正的判据是「锚段的文字内容」。
    """
    body = [line.rstrip() for line in lines]
    while body and (not body[-1].strip() or body[-1].strip() == "---"):
        body.pop()
    return "\n".join(body)


def extract_sections(text: str) -> dict[str, str]:
    """按 `ANCHOR_SECTIONS` 切出各锚段，返回 id → 归一化文本。

    找不到的段落**不出现在结果里**（调用方据此判红），不静默补空串——
    「段落消失」和「段落为空」必须能区分。
    """
    lines = text.splitlines()
    starts: list[tuple[str, int, int]] = []
    for section_id, heading in ANCHOR_SECTIONS:
        for index, line in enumerate(lines):
            if line.lstrip().startswith(heading):
                starts.append((section_id, index, _heading_level(line) or 6))
                break
    starts.sort(key=lambda item: item[1])

    sections: dict[str, str] = {}
    for position, (section_id, index, level) in enumerate(starts):
        stop = len(lines)
        for later_id, later_index, later_level in starts[position + 1 :]:
            del later_id
            if later_level <= level:
                stop = later_index
                break
        else:
            # 后面没有同级标题：退到下一个 `## ` 或文件尾
            for cursor in range(index + 1, len(lines)):
                if _heading_level(lines[cursor]) == 2:
                    stop = cursor
                    break
        sections[section_id] = _normalise(lines[index:stop])
    return sections


def compute_digest(text: str) -> dict[str, str]:
    """id → sha256（对归一化文本）。"""
    return {
        section_id: hashlib.sha256(body.encode("utf-8")).hexdigest()
        for section_id, body in extract_sections(text).items()
    }


def load_digest(path: Path = DIGEST_PATH) -> dict[str, str]:
    """读取登记摘要；结构不对直接抛，不吞。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    sections = payload.get("sections")
    if not isinstance(sections, dict):
        raise ValueError(f"{path}: 缺少 sections 对象")
    return {str(key): str(value) for key, value in sections.items()}


def _looks_like_renamed_anchor(root: Path) -> list[str]:
    """根目录里是否还有「像锚文件」的 .md——用于抓「改名后静默跳过」。"""
    target = anchor_file(root)
    if target.exists():
        return []
    parent = target.parent
    if not parent.is_dir():
        return []
    return sorted(
        path.name
        for path in parent.glob("*.md")
        if "优化方案" in path.name or "optimization" in path.name.lower()
    )


def check(root: Path, digest: dict[str, str] | None = None) -> list[str]:
    """门禁判据：返回问题列表（空 = 通过）。"""
    digest = load_digest() if digest is None else digest
    problems: list[str] = []

    # 「选中 0 段」判红：登记表为空说明判据已失效，不能当作「锚没被改」。
    if not digest:
        problems.append("anchor_digest.json: no sections registered (empty selection is not a pass)")

    unknown = set(digest) - {section_id for section_id, _ in ANCHOR_SECTIONS}
    if unknown:
        problems.append(f"anchor_digest.json: unknown section id(s) {sorted(unknown)}")

    missing_definition = {section_id for section_id, _ in ANCHOR_SECTIONS} - set(digest)
    if missing_definition:
        problems.append(
            f"anchor_digest.json: section(s) declared in code but not registered "
            f"{sorted(missing_definition)}"
        )

    target = anchor_file(root)
    if not target.is_file():
        renamed = _looks_like_renamed_anchor(root)
        if renamed:
            problems.append(
                f"anchor file not found at {target}, but {renamed} look like a renamed anchor "
                f"file (rename the anchor and this gate silently skips)"
            )
        return problems

    found = extract_sections(target.read_text(encoding="utf-8"))
    for section_id, _heading in ANCHOR_SECTIONS:
        if section_id not in found:
            problems.append(
                f"{target.name}: anchor section not found: {section_id} "
                f"(heading renamed or deleted; the anchor must not move silently)"
            )
            continue
        expected = digest.get(section_id)
        if expected is None:
            continue
        actual = hashlib.sha256(found[section_id].encode("utf-8")).hexdigest()
        if actual != expected:
            problems.append(
                f"{target.name}: anchor section changed: {section_id} "
                f"(expected {expected[:12]}, got {actual[:12]}); "
                f"if the change is an approved anchor upgrade, rerun with --update"
            )
    return problems


def _write_digest(sections: dict[str, str]) -> None:
    payload = {
        "_comment": (
            "锚段摘要（工具生成，勿手改）：optimization plan §1 anchor sections. "
            "Regenerate with: python tools/check_anchor_integrity.py --update"
        ),
        "_source": "../优化方案.md",
        "sections": sections,
    }
    DIGEST_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Guard the anchor sections of the root optimization plan by digest."
    )
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--update",
        action="store_true",
        help="rewrite tools/anchor_digest.json from the current anchor text (write mode)",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    root: Path = args.root
    target = anchor_file(root)

    if args.update:
        if not target.is_file():
            print(f"ERROR  anchor file not found: {target}")
            return 1
        sections = compute_digest(target.read_text(encoding="utf-8"))
        missing = {section_id for section_id, _ in ANCHOR_SECTIONS} - set(sections)
        if missing:
            print(f"ERROR  cannot update: anchor section(s) not found {sorted(missing)}")
            return 1
        _write_digest(sections)
        # 摘要文件路径可变（测试用 monkeypatch 指向 tmp_path）：只在仓库内才显示相对路径，
        # 否则直接显示绝对路径 —— 无条件 `relative_to(REPO_ROOT)` 会在仓外路径上抛 ValueError。
        try:
            shown = DIGEST_PATH.relative_to(REPO_ROOT)
        except ValueError:
            shown = DIGEST_PATH
        print(f"updated {shown} ({len(sections)} section(s))")
        return 0

    problems = check(root)
    if args.json:
        print(json.dumps({"ok": not problems, "problems": problems}, ensure_ascii=False))
        return 1 if problems else 0

    if not target.is_file() and not problems:
        # 结构自检已经跑过（登记表非空、无未知 id、无漏登记），只是拿不到锚正文。
        # 明确说出「检查了什么、没检查什么」，不让它变成一条默默变绿的步骤。
        print(
            f"SKIP   anchor text not readable at {target}; "
            f"digest registry structure was still validated "
            f"({len(ANCHOR_SECTIONS)} section(s) declared). "
            f"This gate guards the maintainer's local workspace, not CI checkouts."
        )
        return 0

    if problems:
        for problem in problems:
            print(f"ERROR  {problem}")
        print(f"anchor integrity: {len(problems)} problem(s)")
        return 1
    print("anchor integrity: all anchor sections match the registered digest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
