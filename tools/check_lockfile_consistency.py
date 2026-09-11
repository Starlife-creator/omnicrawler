"""依赖锁一致性：`pyproject.toml` ↔ `uv.lock`（**无需安装 uv** 即可校验）。

对应 `优化方案.md` §7.1 依赖门禁的两条验收：「干净机器按锁文件复现环境」与
「release 产物依赖与锁一致」。前者由 CI 的 `uv sync --locked` 证明；本工具覆盖的是
**在此之前就能发现的那一类问题**——`pyproject.toml` 改了却没重新生成锁文件。

为什么不让 CI 直接跑 `uv lock --check` 就完事：

* 它需要先装 uv，而**贡献者与本地自证**未必有（门禁清单里 `static` 集合要求「干净检出即可跑」）；
* 它只给「不同步」的结论，本工具能指出**具体是哪个依赖/哪个 extras 组**对不上。

因此两者是互补的：本工具进 `static` 门禁（本地与 CI 都跑），`uv lock --check` +
`uv sync --locked` 在专门的 CI job 里做权威复现。

校验项：

1. `requires-python` 一致；
2. 项目版本一致；
3. 顶层 `dependencies` 的名称集合一致；
4. `optional-dependencies` 的**组名集合**一致；
5. 每个 extras 组在锁的 `requires-dist` 里都有对应 marker（组空了说明锁没跟上）。
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
LOCKFILE = REPO_ROOT / "uv.lock"


def _dependency_name(spec: str) -> str:
    """从 PEP 508 依赖串里取出规范化后的包名。"""
    head = spec.split(";", 1)[0].strip()
    for separator in ("[", ">", "<", "=", "!", "~", " ", "(", "@"):
        head = head.split(separator, 1)[0]
    return head.strip().replace("_", "-").casefold()


def _display(path: Path) -> str:
    """尽量显示仓库内相对路径；仓库外（例如测试临时目录）回退为绝对路径。"""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _project_entry(lock: dict, name: str) -> dict | None:
    for entry in lock.get("package", []):
        if str(entry.get("name", "")).casefold() == name.casefold():
            return entry
    return None


def check(pyproject_path: Path = PYPROJECT, lock_path: Path = LOCKFILE) -> list[str]:
    """返回问题列表；空列表表示一致。"""
    if not lock_path.is_file():
        return [f"缺少锁文件: {_display(lock_path)}（运行 `uv lock` 生成）"]
    if not pyproject_path.is_file():
        return [f"缺少 {_display(pyproject_path)}"]

    project = tomllib.loads(pyproject_path.read_text(encoding="utf-8")).get("project", {})
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    issues: list[str] = []

    name = str(project.get("name", ""))
    entry = _project_entry(lock, name)
    if entry is None:
        return [f"锁文件里没有项目自身的条目 {name!r}——请重新生成锁文件"]

    # 1) requires-python
    py_requires = str(project.get("requires-python", ""))
    lock_requires = str(lock.get("requires-python", ""))
    if py_requires != lock_requires:
        issues.append(
            f"requires-python 不一致：pyproject={py_requires!r} lock={lock_requires!r}"
        )

    # 2) 版本
    py_version = str(project.get("version", ""))
    lock_version = str(entry.get("version", ""))
    if py_version != lock_version:
        issues.append(f"项目版本不一致：pyproject={py_version!r} lock={lock_version!r}")

    # 3) 顶层依赖
    declared = {_dependency_name(item) for item in project.get("dependencies", [])}
    locked = {str(item.get("name", "")).casefold() for item in entry.get("dependencies", [])}
    if declared - locked:
        issues.append(
            "顶层依赖未进入锁文件: " + ", ".join(sorted(declared - locked)) + "（改完 pyproject 需重新 `uv lock`）"
        )
    if locked - declared:
        issues.append("锁文件里的顶层依赖已从 pyproject 移除: " + ", ".join(sorted(locked - declared)))

    # 4) extras 组名
    declared_extras = set(project.get("optional-dependencies", {}))
    locked_extras = set(entry.get("optional-dependencies", {}))
    if declared_extras - locked_extras:
        issues.append(
            "pyproject 里的 extras 组未进入锁文件: " + ", ".join(sorted(declared_extras - locked_extras))
        )
    if locked_extras - declared_extras:
        issues.append(
            "锁文件里的 extras 组已从 pyproject 移除: " + ", ".join(sorted(locked_extras - declared_extras))
        )

    # 5) extras 组在 requires-dist 里有 marker（组空了说明锁没跟上）
    requires_dist = entry.get("metadata", {}).get("requires-dist", [])
    extras_with_marker: set[str] = set()
    for item in requires_dist if isinstance(requires_dist, list) else []:
        marker = str(item.get("marker", ""))
        for extra in declared_extras:
            if f"extra == '{extra}'" in marker or f'extra == "{extra}"' in marker:
                extras_with_marker.add(extra)
    empty_extras = sorted(declared_extras - extras_with_marker)
    if empty_extras:
        issues.append(
            "以下 extras 组在锁文件里没有任何依赖记录（可能是空组或锁未跟上）: "
            + ", ".join(empty_extras)
        )
    return issues


def main() -> int:
    issues = check()
    if issues:
        print(f"依赖锁一致性检查未通过（{len(issues)} 项）：")
        for issue in issues:
            print(f"  - {issue}")
        print("\n修复：在仓库根运行 `uv lock` 重新生成 uv.lock，再复核本检查。")
        return 1
    print("依赖锁一致性检查通过：pyproject.toml 与 uv.lock 同步（版本 / requires-python / 依赖 / extras）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
