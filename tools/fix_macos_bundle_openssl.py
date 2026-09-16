#!/usr/bin/env python3
"""macOS 便携包：把 `_ssl` 的动态库依赖从**别的包自带的同名库**改回正确的 OpenSSL。

## 事故（2026-09-16，`release.yml` 无参派发 `35089212498`）

```
ImportError: dlopen(.../Contents/Frameworks/python3.12/lib-dynload/_ssl.cpython-312-darwin.so):
  Symbol not found: _X509_STORE_get1_objects
Referenced from: .../lib-dynload/_ssl.cpython-312-darwin.so
Expected in:     .../Contents/Frameworks/cv2/__dot__dylibs/libcrypto.3.dylib   ← ★ 元凶
```

`macOS-Full` 的 spec 用 `collect_all("cv2")` 收集 cv2 的动态库，而 **cv2 自带一份
`libcrypto.3.dylib`**（与 `_ssl` 需要的那份**同名**）⇒ PyInstaller 去重/绑定时让 `_ssl`
指向了 cv2 的副本，版本偏旧、缺 OpenSSL 3 的新符号 ⇒ `_ssl` 导入失败 ⇒ `ssl` 不可用。

**为什么只有 Full 版失败**：`macOS`（Standard）spec 的 `excludes` 里有 `cv2`，所以没有这份冲突；
Linux/Windows 的包结构与 dylib 命名不同，也没有这个撞名。

## 这个脚本做什么（确定性、最小改动）

1. 在产物里找 `_ssl*.so`；
2. 用 `otool -L` 读出它对 `libcrypto*.dylib` / `libssl*.dylib` 的依赖；
3. **只在**该依赖指向「另一个包自带的 dylibs 目录」（如 `*/cv2/.dylibs/…`）时才动手：
   把**构建期 Python 自带的那份**（`sys.prefix/lib/…`，符号齐全）复制进产物
   `Contents/Frameworks/openssl/`，再用 `install_name_tool -change` 把 `_ssl` 的依赖指过去；
4. 全程打印做了什么；**改不动就非零退出**（让 CI 当场红，而不是产出一个 ssl 坏的包）。

★ 只改 `_ssl` 这一个模块的依赖，不动 cv2 自己的库（cv2 仍用它自带的副本，互不干扰）。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

_TARGET_PATTERNS = ("libcrypto*.dylib", "libssl*.dylib")


def _run(cmd: list[str], *, check: bool = True) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} 失败：{proc.stderr.strip()[:300]}")
    return proc.stdout


def _find_ssl_module(bundle: Path) -> Path | None:
    for pattern in ("**/_ssl*.so", "**/_ssl*.dylib"):
        hits = [p for p in bundle.glob(pattern) if p.is_file()]
        if hits:
            return hits[0]
    return None


def _dependencies(module: Path) -> dict[str, str]:
    """返回 {依赖名: 解析到的绝对路径}（`otool -L` 的第二列是解析结果）。"""
    deps: dict[str, str] = {}
    for line in _run(["otool", "-L", str(module)]).splitlines()[1:]:
        parts = line.strip().split(" (compat")
        if len(parts) != 2:
            continue
        target = parts[0].strip()
        name = Path(target).name
        if any(Path(name).match(pat) for pat in _TARGET_PATTERNS):
            deps[name] = target
    return deps


def _source_libs() -> dict[str, Path]:
    """构建期**正确**的那份 OpenSSL 动态库（符号齐全）。

    ★ 实测（2026-09-16，`actions/setup-python` 的 macOS framework Python）：
    `sys.prefix/lib` 里**没有**这些库 —— 它们由 **Homebrew 的 openssl@3** 提供，
    或只体现在 `_ssl` 自身的依赖路径里。因此按**权威度**依次找：

    1. **构建期 `_ssl` 模块自身的依赖路径**（最权威：就是解释器实际加载的那份）；
    2. `sys.prefix/lib`、`sys.base_prefix/lib`（自编 Python / pyenv 常见）；
    3. Homebrew 常见位置（`/opt/homebrew/opt/openssl@3/lib`、`/usr/local/opt/openssl@3/lib`）。
    """
    found: dict[str, Path] = {}

    # ① 构建期 `_ssl` 自身依赖（只依赖 otool；非 macOS 环境不可用 ⇒ 静默跳过）
    try:
        import _ssl

        module = Path(str(_ssl.__file__))
        if module.is_file():
            for name, resolved in _dependencies(module).items():
                candidate = Path(resolved)
                if candidate.is_file():
                    found.setdefault(name, candidate)
    except Exception:  # noqa: BLE001 —— 拿不到就退到下面的候选位置
        pass

    # ② 解释器前缀；③ Homebrew
    candidates = [
        Path(sys.prefix) / "lib",
        Path(sys.base_prefix) / "lib",
        Path("/opt/homebrew/opt/openssl@3/lib"),
        Path("/usr/local/opt/openssl@3/lib"),
    ]
    for base in candidates:
        if not base.is_dir():
            continue
        for pattern in _TARGET_PATTERNS:
            for path in base.glob(pattern):
                resolved = Path(path).resolve()
                if resolved.is_file():
                    found.setdefault(path.name, resolved)
    return found


def _is_foreign(dep_name: str, resolved: str) -> bool:
    """该依赖是否指向「别的包自带的 dylibs 目录」（即需要改的那个场景）。"""
    marker = f"/{dep_name}"
    if not resolved.endswith(marker):
        return False
    parent = Path(resolved).parent
    return "/.dylibs" in resolved or parent.name.startswith(".")


def repair(bundle: Path, *, dry_run: bool = False) -> int:
    module = _find_ssl_module(bundle)
    if module is None:
        print(f"未在产物里找到 _ssl 模块：{bundle}")
        return 2

    deps = _dependencies(module)
    if not deps:
        print(f"{module.name}: 未解析到 libcrypto/libssl 依赖，无需处理")
        return 0

    sources = _source_libs()
    if not sources:
        print("构建期 Python 下找不到 libcrypto/libssl（无法提供正确副本）")
        return 3

    target_dir = bundle / "Contents" / "Frameworks" / "openssl"
    changed = 0
    for name, resolved in deps.items():
        foreign = _is_foreign(name, resolved)
        print(f"  {name}: {resolved}{'（★ 指向别的包自带副本）' if foreign else '（正常）'}")
        if not foreign:
            continue
        source = sources.get(name)
        if source is None:
            print(f"  ✗ 构建期没有 {name} 可替换")
            return 4
        if dry_run:
            print(f"  [dry-run] 将把 {name} 从 {source} 复制到 {target_dir}，并改 {module.name} 的依赖")
            changed += 1
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
        destination = target_dir / name
        shutil.copy2(source, destination)
        _run(["install_name_tool", "-change", resolved, str(destination), str(module)])
        changed += 1
        print(f"  ✓ 已把 {module.name} 的 {name} 依赖改到 {destination}")

    if changed and not dry_run:
        # 改过 Mach-O 必须重新签名（ad-hoc），否则签名失效
        _run(["codesign", "--force", "--sign", "-", str(module)])
        print(f"  ✓ 已对 {module.name} 重新 ad-hoc 签名")
    print(f"完成：处理 {changed} 个依赖")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="修 macOS 便携包里 _ssl 的 OpenSSL 依赖指向")
    parser.add_argument("bundle", type=Path, help=".app 路径或 release 根")
    parser.add_argument("--dry-run", action="store_true", help="只报告不改动")
    args = parser.parse_args(argv)
    bundle = args.bundle
    if (bundle / "Contents").is_dir():
        return repair(bundle, dry_run=args.dry_run)
    # 传的是 release 根：找里面的 .app
    apps = list(bundle.glob("**/*.app"))
    if not apps:
        print(f"{bundle} 下找不到 .app")
        return 2
    return repair(apps[0], dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
