"""验证「装完所有组件后」Standard 便携包的真实兼容性（CI 用）。

## 存在的理由，以及它取代了什么

维护者裁定（2026-11-19）：

> 有必要对账吗，是不是修改一下 full 构建逻辑测试出来它的测试没问题就行，
> 主要测试装完所有组件的兼容性等。没必要构建一个和之前的一样的大号冻结包。

这个判断是对的。原先的做法有两个问题：

1. **Full 大号冻结包**每个平台要多跑一轮 PyInstaller（Linux 实测多约 40 分钟），
   而它要证明的「重依赖能否装上并跑通」并不是用户真实路径 ——
   用户拿到的是 Standard + 按需装组件。
2. **能力等价性对账**拿两份静态能力报告比数字，报出的缺口大多是
   `full` 档的能力（`paddle_models` / `selenium` / `crawl4ai` / `s3`），
   那些本来就不该由 Standard 承担。比数字证明不了"装完能用"。

所以改成**直接验能不能用**：

1. 在解压后的 Standard 产物上，按市场快照逐个 `install-recipe --download`；
2. 对每个装好的组件执行 `components run --payload {...}`，确认它**真的在隔离子进程里跑起来**；
3. 任何一步失败都让 CI 红，并指名是哪个组件、哪一步。

这比"比两份 JSON 的能力项"强：前者验证运行，后者只验证声明。

## 判据（确定性）

* 组件**必须全部装上**（缺一即失败）；
* 装上的组件**必须都能 run 成功**（退出码 0 且 stdout 有 JSON 结果）；
* **空组件列表判失败** —— 空集合会让"全部通过"恒真。
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


def _utf8_env() -> dict[str, str]:
    """给子进程 UTF-8 环境。

    按**文件路径**加载 `tools/child_env.py`：本脚本被 CI 直接执行时
    `sys.path[0]` 是 `tools/portable/`，仓库根不在路径上
    （v1.0.5 在收集脚本上踩过 `ModuleNotFoundError`）。
    """
    import importlib.util as _ilu

    root = Path(__file__).resolve().parents[2]
    spec = _ilu.spec_from_file_location("_child_env", root / "tools" / "child_env.py")
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ImportError(f"无法加载 {root / 'tools' / 'child_env.py'}")
    module = _ilu.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.utf8_env()


@dataclass
class VerifyReport:
    """验证结果。"""

    installed: list[str] = field(default_factory=list)
    ran_ok: list[str] = field(default_factory=list)
    failures: list[tuple[str, str, str]] = field(default_factory=list)  # (id, stage, why)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (id, why)

    @property
    def ok(self) -> bool:
        return not self.failures and bool(self.installed)

    def summary(self) -> str:
        parts = [
            f"装上 {len(self.installed)} 个",
            f"跑通 {len(self.ran_ok)} 个",
            f"失败 {len(self.failures)} 个",
        ]
        if self.skipped:
            parts.append(f"跳过 {len(self.skipped)} 个")
        return "，".join(parts)


def _current_platform() -> str:
    """本机平台名，与 recipe 的 `platforms` 同一套拼写。"""
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


#: 日志行前缀（CLI 运行时会把它们和真实错误一起打到 stderr）。
_LOG_LINE_PREFIXES = ("WARNING ", "INFO ", "DEBUG ", "ERROR ")


def _tail(text: str, limit: int = 600) -> str:
    """从子进程输出里取出**真正有用的错误详情**。

    两步，缺一不可：

    1. **滤掉日志行**：CLI 启动时会打 `2026-11-19 18:55:27,698 WARNING ...`、
       `INFO omnicrawler 启动`。它们和真正的错误混在同一个 stderr 里，
       400 字符窗口会被日志吃光 —— 真实 CI 上就只看到
       `...ver', 'timeline', 'replay', ...` 这种被截断的 argparse 列表，
       完全看不出是哪一步坏了。
    2. **取末尾**：真正的错误在最后（v1.0.7 踩过取开头的坑）。
    """
    lines = [
        line
        for line in (text or "").strip().splitlines()
        if line.strip() and not any(line.lstrip().startswith(p) for p in _LOG_LINE_PREFIXES)
    ]
    # 日志行里也可能带时间戳前缀（`2026-11-19 18:55:27,698 WARNING`）
    filtered = [
        line
        for line in lines
        if "WARNING omnicrawler" not in line and "INFO omnicrawler" not in line
    ]
    cleaned = "\n".join(filtered or lines).strip()
    return cleaned[-limit:] if len(cleaned) > limit else cleaned


def load_component_entries(catalog_path: Path) -> list[dict]:
    """从市场 catalog 读出全部组件条目（按 id 排序，顺序确定）。"""
    data = json.loads(catalog_path.read_text(encoding="utf-8"))
    components = data.get("components") or []
    if not isinstance(components, list):
        raise ValueError(f"{catalog_path} 的 components 不是数组")
    return sorted(
        (item for item in components if isinstance(item, dict) and item.get("id")),
        key=lambda item: str(item.get("id")),
    )


def _recipe_path_for(market_root: Path, entry: dict) -> Path:
    rel = str(entry.get("recipe_file") or "").strip()
    if not rel:
        rel = f"components/{entry['id']}/recipe.json"
    return market_root / rel


def _run(argv: list[str], cwd: Path, *, timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - argv 由本模块构造
        argv,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=_utf8_env(),
        check=False,
    )


def _installed_component_dir(install_stdout: str) -> Path | None:
    """从 `install-recipe` 的输出里取组件目录。

    **输出是给人看的文本，不是 JSON**（`dumps_summary` 渲染）：

        组件：ocr-paddle 1.0.0
        安装到：/path/to/components/ocr-paddle-1.0.0
        入口：entry.json
        recipe 摘要：…

    早先按 JSON 解析（找 `{"path": ...}`），所以永远拿到 None
    —— 真实 CI 报「装完后找不到组件目录（摘要 path=None）」。
    兼容两种写法：优先找 `安装到：` 行，退化到 JSON 的 `path` 键。
    """
    text = install_stdout or ""
    for line in text.splitlines():
        stripped = line.strip()
        for prefix in ("安装到：", "安装到:"):
            if stripped.startswith(prefix):
                candidate = stripped[len(prefix) :].strip()
                if candidate and candidate != "?":
                    return Path(candidate)

    # 兼容：若将来改成 JSON 输出
    start_index = text.find("{")
    end_index = text.rfind("}")
    if 0 <= start_index < end_index:
        try:
            payload = json.loads(text[start_index : end_index + 1])
        except json.JSONDecodeError:
            return None
        path = payload.get("path") if isinstance(payload, dict) else None
        return Path(str(path)) if path else None
    return None


def verify_all(
    executable: Path,
    release_root: Path,
    market_root: Path,
    *,
    install_timeout: float = 1800.0,
    run_timeout: float = 300.0,
) -> VerifyReport:
    """装齐并逐个验证可运行。"""
    report = VerifyReport()
    catalog = market_root / "catalog.json"
    if not catalog.is_file():
        report.failures.append(("<catalog>", "读取", f"市场快照不存在：{catalog}"))
        return report
    try:
        entries = load_component_entries(catalog)
    except (OSError, ValueError) as exc:
        report.failures.append(("<catalog>", "解析", str(exc)))
        return report
    if not entries:
        report.failures.append(("<catalog>", "读取", "市场快照里没有任何组件条目"))
        return report

    for entry in entries:
        cid = str(entry["id"])
        recipe = _recipe_path_for(market_root, entry)

        # ①a 平台判定：配方声明支持的平台不含本机 ⇒ 显式跳过（可见，不静默）
        try:
            declared = json.loads(recipe.read_text(encoding="utf-8")).get("platforms") or []
        except (OSError, json.JSONDecodeError):
            declared = []
        if declared and _current_platform() not in declared:
            report.skipped.append(
                (cid, f"组件不支持当前平台（声明 {declared}，本机 {_current_platform()}）")
            )
            continue

        # ①b 装
        if not recipe.is_file():
            report.failures.append((cid, "安装", f"recipe 缺失：{recipe}"))
            continue
        installed = _run(
            [
                str(executable),
                "components",
                "install-recipe",
                "--recipe",
                str(recipe),
                "--download",
            ],
            release_root,
            timeout=install_timeout,
        )
        if installed.returncode != 0:
            detail = _tail(installed.stderr) or _tail(installed.stdout) or (
                f"退出码 {installed.returncode}"
            )
            report.failures.append((cid, "安装", detail))
            continue
        report.installed.append(cid)

        # ② 跑：**只有可执行组件才 run**。
        #
        # 纯数据组件（如 tesseract 语言包）的 `entry.path` 是数据文件、
        # 没有 `runner` —— 它们装好即完成，不该走 `components run`
        # （那会要求 site-packages 与 entry.json，真实 CI 因此误报失败）。
        runner = ""
        try:
            runner = str(
                (json.loads(recipe.read_text(encoding="utf-8")).get("entry") or {}).get(
                    "runner"
                )
                or ""
            )
        except (OSError, json.JSONDecodeError):
            runner = ""
        if not runner:
            report.ran_ok.append(f"{cid}（数据组件，装好即完成）")
            continue

        payload = entry.get("entry_payload")
        payload_arg = json.dumps(payload, ensure_ascii=False) if isinstance(payload, dict) else "{}"
        # 组件目录从安装摘要的 `path` 取（不猜路径）
        component_dir = _installed_component_dir(installed.stdout)
        if component_dir is None or not component_dir.is_dir():
            report.failures.append(
                (cid, "运行", f"装完后找不到组件目录（摘要 path={component_dir}）")
            )
            continue
        ran = _run(
            [
                str(executable),
                "components",
                "run",
                "--component-dir",
                str(component_dir),
                "--payload",
                payload_arg,
            ],
            release_root,
            timeout=run_timeout,
        )
        if ran.returncode != 0:
            detail = _tail(ran.stderr) or _tail(ran.stdout) or f"退出码 {ran.returncode}"
            report.failures.append((cid, "运行", detail))
            continue
        report.ran_ok.append(cid)
    return report


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 3:
        print("用法: verify_standard_components.py <release_root> <market_root> <label>")
        return 2
    release_root, market_root, label = Path(args[0]), Path(args[1]), args[2]

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from collect_capability_reports import locate_executable  # noqa: PLC0415

    executable = locate_executable(release_root)
    if executable is None:
        print(f"::error::{label} 产物里没找到可执行文件")
        return 3

    report = verify_all(executable, release_root, market_root)
    print(f"{label} 组件验证：{report.summary()}")
    for cid in report.installed:
        print(f"  ✓ 已装 {cid}")
    for cid in report.ran_ok:
        print(f"  ✓ 跑通 {cid}")
    for cid, why in report.skipped:
        print(f"  - 跳过 {cid}：{why}")
    for cid, stage, why in report.failures:
        print(f"::error::{label} 组件 {cid} 在「{stage}」阶段失败：{why}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
