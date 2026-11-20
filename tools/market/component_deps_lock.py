"""依赖闭包快照：把「网上的事实」固化成仓内文件。

## 为什么需要这个工具

组件的 `recipe.json` 手写列出每个 wheel。而一个包的真实依赖闭包要联网查 PyPI 才
知道 —— 手写必然漏（v1.0.23→v1.0.27 连续 5 轮都在补漏掉的包：paddlex、colorlog、
urllib3、annotated-types …）。

**但本地不该联网**（维护者的开发机，且 105 个 wheel 约 320 MB 不能本地拉）。
所以把两边拆开：

* **本工具（联网、慢）**：算一次闭包，写进 `deps.lock.json` 入库。
  由 CI 定期跑或人工触发，**不在 static 门禁里**。
* **门禁（零网络、毫秒级）**：读快照 + 读配方，只做集合包含判定。

## 用法

```bash
# 算一份快照（需要联网）。--root 指向含 recipe.json 的组件目录
python tools/market/component_deps_lock.py --recipe market/components/ocr-paddle/recipe.json

# 校验快照与配方一致（零网络，门禁用这个）
python tools/market/component_deps_lock.py --recipe ... --check
```

## 判据（--check 模式）

1. 快照里列出的**每个包**，配方里都必须有对应 wheel 文件名；
2. 快照必须声明它对应的 `recipe_digest` —— 配方变了而快照没重算，**判红**
   （否则「快照过期」会被静默当成「通过」）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any

#: 环境标记评估口径（CI 与本机都按 linux/cp313 算；只影响条件依赖的取舍）。
_ENV = {"python_version": "3.13", "sys_platform": "linux", "platform_system": "Linux"}

#: 不需要单独装的标准库/特殊项。
_BUILTIN = {"argparse", "typing", "dataclasses"}


def _normalize(name: str) -> str:
    """PEP 503 规范名：`-` / `.` / `_` 等价。"""
    return re.sub(r"[-_.]+", "-", name).lower()


def _marker_ok(marker: str) -> bool:
    """粗判环境标记是否在当前口径成立。只处理最常见的 python_version 比较。"""
    marker = marker.strip()
    if not marker:
        return True
    match = re.search(r'python_version\s*(>=|<=|==|<|>)\s*"([\d.]+)"', marker)
    if not match:
        return True
    op, want = match.group(1), match.group(2)
    have = _ENV["python_version"]
    have_t = tuple(int(x) for x in have.split("."))
    want_t = tuple(int(x) for x in want.split("."))
    return {
        ">=": have_t >= want_t,
        "<=": have_t <= want_t,
        "==": have_t == want_t,
        "<": have_t < want_t,
        ">": have_t > want_t,
    }[op]


def _dep_names(requires: list[str] | None) -> list[str]:
    """从 requires_dist 抽出本口径下需要的包名。"""
    out: list[str] = []
    for raw in requires or []:
        if "extra ==" in raw:
            continue
        head, _, marker = raw.partition(";")
        if not _marker_ok(marker):
            continue
        name = re.split(r"[<>=!\[(]", head.strip(), maxsplit=1)[0].strip()
        if name and _normalize(name) not in _BUILTIN:
            out.append(name)
    return out


def compute_closure(seed_packages: list[str]) -> dict[str, list[str]]:
    """递归展开闭包，返回 `{包名: 它的直接依赖}`（已规范化）。"""
    edges: dict[str, list[str]] = {}
    queue = list(seed_packages)
    while queue:
        name = queue.pop()
        key = _normalize(name)
        if key in edges:
            continue
        try:
            with urllib.request.urlopen(
                f"https://pypi.org/pypi/{name}/json", timeout=30
            ) as response:
                data = json.load(response)
        except Exception:  # noqa: BLE001 - 查不到就记空，不中断整轮
            edges[key] = []
            continue
        deps = _dep_names(data["info"].get("requires_dist"))
        edges[key] = [_normalize(d) for d in deps]
        for dep in deps:
            if _normalize(dep) not in edges:
                queue.append(dep)
    return edges


def _wheel_names(recipe: dict[str, Any]) -> str:
    """把配方里所有 wheel 文件名拼成一串（用于子串匹配）。"""
    return " ".join(
        str(a.get("url") or "").rsplit("/", 1)[-1].lower() for a in recipe.get("artifacts") or []
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="组件依赖闭包快照")
    parser.add_argument("--recipe", required=True, type=Path)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--check", action="store_true", help="只校验，不联网")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    recipe_path: Path = args.recipe
    if not recipe_path.is_file():
        print(f"配方不存在：{recipe_path}", file=sys.stderr)
        return 2
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from omnicrawler.services.component_recipe import load_recipe, recipe_digest

    recipe = load_recipe(recipe_path)
    digest = recipe_digest(recipe)
    out_path = args.out or recipe_path.parent / "deps.lock.json"

    if args.check:
        if not out_path.is_file():
            print(f"缺少依赖闭包快照：{out_path}", file=sys.stderr)
            return 1
        snapshot = json.loads(out_path.read_text(encoding="utf-8"))
        if snapshot.get("recipe_digest") != digest:
            print(
                "依赖闭包快照已过期：配方的 recipe_digest 变了但快照没重算。"
                "请运行 component_deps_lock.py 重新生成。",
                file=sys.stderr,
            )
            return 1
        haystack = _wheel_names(recipe)
        missing = [
            name
            for name in snapshot.get("packages") or []
            if _normalize(name) not in haystack.replace("-", "_").replace(".", "_")
            and _normalize(name).replace("-", "_") not in haystack.replace("-", "_")
        ]
        if missing:
            print(f"配方缺少闭包声明的包：{missing}", file=sys.stderr)
            return 1
        print(f"依赖闭包一致：{len(snapshot.get('packages') or [])} 个包")
        return 0

    # 算闭包：种子 = 配方里 wheel 名对应的包（去掉版本与标签）
    seeds: list[str] = []
    for artifact in recipe.get("artifacts") or []:
        filename = str(artifact.get("url") or "").rsplit("/", 1)[-1]
        if not filename.endswith((".whl", ".tar.gz")):
            continue
        seeds.append(filename.split("-")[0])
    if not seeds:
        print("配方里没有可识别的 wheel 文件名，无法算闭包", file=sys.stderr)
        return 2

    edges = compute_closure(seeds)
    closure = sorted({pkg for deps in edges.values() for pkg in deps} | set(edges))
    snapshot = {
        "_comment": "依赖闭包快照（工具生成，勿手改）：改配方后请重新运行 component_deps_lock.py",
        "recipe_digest": digest,
        "python_version": _ENV["python_version"],
        "packages": closure,
    }
    out_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"已写入 {out_path}：{len(closure)} 个包")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
