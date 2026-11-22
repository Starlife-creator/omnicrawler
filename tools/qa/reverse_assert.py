"""反向断言工具：证明一条守卫**真的会红**。

## 这个工具解决什么

判据纪律要求「新守卫必须做反向断言：装回缺口 ⇒ 必须红 ⇒ 字节级还原」。
只绿不红等于没装护栏 —— 守卫可能恒真、可能根本没被触发。

但手工做这件事有 4 个步骤：

1. 备份文件（并算出 sha256）
2. 把缺口装回去（改源码）
3. 跑测试，确认变红
4. 还原，确认 sha256 与测试都回到原样

**本会话我因为步骤多，只在快测试上做了反向断言** ——
组件相关的新守卫基本没逐条验证。这是纪律上的亏欠，根因是成本太高。

本工具把 4 步收成 1 步，并且**强制**做字节级校验：还原后必须与备份的
sha256 完全一致，否则不认为成功（防止「改回来了但少了个空行」）。

## 用法

```powershell
python tools/qa/reverse_assert.py `
    --file src/omnicrawler/services/component_recipe.py `
    --find 'if resolved.is_file() or resolved.is_dir():' `
    --replace 'if resolved.is_file():' `
    --test tests/unit/services/test_component_native.py
```

流程：
1. 记录 `--file` 的 sha256
2. 断言 `--find` 在文件中**恰好出现一次**（避免改错位置）
3. 替换为 `--replace`
4. 跑 `--test`：**必须失败**（退出码非 0）
5. 还原文件，重新算 sha256 并与步骤 1 比对
6. 再跑 `--test`：**必须通过**

任一步不符合预期就报错退出，并**保证文件已还原**（`finally`）。

## 为什么必须校验 sha256

只比较「跑完测试又绿了」不够：如果还原时丢了空行或改了编码，
那条守卫可能仍绿但实现已经受损。字节级比对是唯一可信的等价判据。
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

OK_MARK = "[OK]"
BAD_MARK = "[FAIL]"

_ROOT = Path(__file__).resolve().parents[2]

# ★ **每个 tools/ 下的脚本都要这一段**。
# 直接运行 `python tools/qa/xxx.py` 时 `sys.path[0]` 是**脚本所在目录**
# （这里是 `tools/qa/`），不是仓库根 —— 于是 `import tools.child_env`
# 必然失败。这个坑我在本会话里踩了四次（fast_check / check_component_recipes
# / check_workspace_map / 本文件），所以固化在模板里。
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _run_pytest(target: str, timeout: int) -> tuple[int, str]:
    python = _ROOT / ".venv" / "Scripts" / "python.exe"
    exe = str(python) if python.is_file() else sys.executable
    from tools.child_env import utf8_env

    result = subprocess.run(
        [exe, "-m", "pytest", target, "-q", "--no-header", "-x"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=utf8_env(),
        timeout=timeout,
        check=False,
    )
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def _tail(text: str, limit: int = 400) -> str:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return "\n".join(lines[-6:])[:limit]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="反向断言：破坏源码 ⇒ 测试必须红 ⇒ 还原 ⇒ 必须绿",
    )
    parser.add_argument("--file", required=True, help="要破坏的源文件")
    parser.add_argument("--find", required=True, help="原文片段（必须恰好出现一次）")
    parser.add_argument("--replace", required=True, help="替换成的片段（装回缺口）")
    parser.add_argument("--test", required=True, help="用来验证的测试目标")
    parser.add_argument("--timeout", type=int, default=600, help="每次跑测试的超时秒数")
    args = parser.parse_args(argv)

    target = Path(args.file)
    if not target.is_absolute():
        target = _ROOT / target
    if not target.is_file():
        print(f"{BAD_MARK} 文件不存在：{target}")
        return 2

    original_bytes = target.read_bytes()
    original_sha = hashlib.sha256(original_bytes).hexdigest()
    text = original_bytes.decode("utf-8")

    occurrences = text.count(args.find)
    if occurrences != 1:
        print(
            f"{BAD_MARK} `--find` 在 {target.name} 里出现 {occurrences} 次"
            f"（必须恰好 1 次）—— 改错位置的代价比不改更大"
        )
        return 2

    try:
        # ── 第 1 步：跑一遍确认当前是绿的（否则反向断言没有意义） ──────
        print("[1/5] 确认当前测试是绿的…")
        code, output = _run_pytest(args.test, args.timeout)
        if code != 0:
            print(f"{BAD_MARK} 测试当前就是红的，反向断言无意义：")
            print(_tail(output))
            return 1

        # ── 第 2 步：破坏 ────────────────────────────────────────────
        print("[2/5] 装回缺口…")
        target.write_bytes(text.replace(args.find, args.replace, 1).encode("utf-8"))

        # ── 第 3 步：必须红 ──────────────────────────────────────────
        print("[3/5] 确认测试变红…")
        code, output = _run_pytest(args.test, args.timeout)
        if code == 0:
            print(
                f"{BAD_MARK} 装了缺口之后测试**仍然是绿的** —— "
                "这条守卫没有真正守住该守的东西"
            )
            return 1
        print(f"      变红 ✅（退出码 {code}）")

    finally:
        # ── 第 4 步：无条件还原 ──────────────────────────────────────
        target.write_bytes(original_bytes)

    # ── 第 4 步校验：字节级一致 ──────────────────────────────────────
    print("[4/5] 校验还原后的字节…")
    restored_sha = _sha256(target)
    if restored_sha != original_sha:
        print(
            f"{BAD_MARK} 还原后字节不一致！\n"
            f"  原始: {original_sha[:16]}…\n"
            f"  当前: {restored_sha[:16]}…"
        )
        return 1
    print(f"      字节一致 ✅（{restored_sha[:16]}…）")

    # ── 第 5 步：必须绿 ──────────────────────────────────────────────
    print("[5/5] 确认还原后测试重新变绿…")
    code, output = _run_pytest(args.test, args.timeout)
    if code != 0:
        print(f"{BAD_MARK} 还原后测试仍是红的：")
        print(_tail(output))
        return 1

    print(f"\n{OK_MARK} 反向断言通过：这条守卫真的会红，且还原是字节级的")
    return 0


if __name__ == "__main__":
    sys.exit(main())
