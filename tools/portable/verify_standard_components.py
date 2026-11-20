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
from typing import Any


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
#: 日志行前缀。要覆盖两种写法：Python logging 的 `WARNING xxx`（空格）
#: 与自定义格式的 `WARNING: xxx`（冒号）—— 早先只认前者，后者全被当成错误详情。
#:
#: **只看带模块名的 Python logging 形态**（`WARNING omnicrawler.xxx`、
#: `INFO omnicrawler ...`）。不按裸 `ERROR:` 过滤 —— 那样会把业务错误
#: （如 `ERROR: 组件装配失败`）一起丢掉，结果什么都没剩下。
_LOG_LINE_PREFIXES = (
    "WARNING omnicrawler",
    "INFO omnicrawler",
    "DEBUG omnicrawler",
    "ERROR omnicrawler",
)


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
    raw_lines = (text or "").strip().splitlines()
    kept: list[str] = []
    for line in raw_lines:
        if not line.strip():
            continue
        lowered = line.lower()
        # 带模块名的 logging 行（`2026-... WARNING omnicrawler.xxx`）
        if any(marker in lowered for marker in (" warning omnicrawler", " info omnicrawler", " debug omnicrawler")):
            continue
        if any(line.lstrip().startswith(p) for p in _LOG_LINE_PREFIXES):
            continue
        # 同一条消息还会以不带时间戳的形式重复一次（`内置 Tesseract 缺失: ...`）——
        # 它们是**告警**，不是失败原因。滤掉，否则真实错误永远被挤在窗口外。
        if lowered.startswith(("内置 ", "内置tesseract", "内置chromedriver")):
            continue
        kept.append(line)
    cleaned = "\n".join(kept or raw_lines).strip()
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


def _parse_smoke(recipe_data: dict) -> Any:
    """从配方数据解析 `smoke`（复用宿主实现，不在这里重复一套判据）。"""
    from omnicrawler.services.component_smoke import parse_smoke

    return parse_smoke(recipe_data)


def _verify_expect(smoke: Any, stdout: str) -> list[str]:
    """核对组件输出是否满足 `smoke.expect`。"""
    from omnicrawler.services.component_smoke import verify_result

    text = (stdout or "").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return [f"组件输出不是 JSON：{text[:200]!r}"]
    try:
        result = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        return [f"组件输出不是合法 JSON：{exc}"]
    return verify_result(smoke, result)


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

        # ② 跑：**判据由组件自己的 `smoke` 声明决定**，验证器不再猜。
        #
        # 早先用「有没有 entry.runner」推断组件类型 —— 那是宿主的猜测，
        # 而且每加一个组件都要改本脚本。现在读 `smoke.skip_run` 与
        # `smoke.expect`，本脚本只负责执行与核对。
        try:
            recipe_data = json.loads(recipe.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            report.failures.append((cid, "运行", f"配方无法解析：{exc}"))
            continue
        try:
            smoke = _parse_smoke(recipe_data)
        except Exception as exc:  # noqa: BLE001 - smoke 声明不合法要如实报
            report.failures.append((cid, "运行", f"smoke 声明不合法：{exc}"))
            continue
        if smoke.skip_run:
            report.ran_ok.append(f"{cid}（{smoke.description or '声明 skip_run，装好即完成'}）")
            continue

        payload_arg = json.dumps(smoke.payload, ensure_ascii=False)
        run_timeout = max(run_timeout, smoke.timeout_seconds)
        # 组件目录从安装摘要的 `path` 取（不猜路径）
        # 摘要可能打在 stdout 也可能在 stderr（取决于 CLI 的日志分流）——两处都看，
        # 否则会报「装完后找不到组件目录（摘要 path=None）」但日志明明有「安装到：」。
        component_dir = _installed_component_dir(installed.stdout) or _installed_component_dir(
            installed.stderr
        )
        if component_dir is None or not component_dir.is_dir():
            # 把安装输出摘出来，便于定位「摘要里到底有没有 安装到：」
            preview = _tail((installed.stdout or "") + (installed.stderr or ""), 300)
            report.failures.append(
                (
                    cid,
                    "运行",
                    f"装完后找不到组件目录（path={component_dir}）；安装输出尾部：{preview}",
                )
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
        if ran.returncode == 0:
            # 退出码 0 还不够 —— 必须核对组件承诺的 expect
            unmet = _verify_expect(smoke, ran.stdout)
            if unmet:
                report.failures.append((cid, "运行", "；".join(unmet)))
                continue
            report.ran_ok.append(cid)
            continue

        if ran.returncode != 0:
            # 诊断要点：**先给退出码与命令形态**，再给输出尾部。
            # 否则日志里只剩一坨被截断的 argparse 列表，看不出是哪一步。
            detail = _tail(ran.stderr) or _tail(ran.stdout) or "(无输出)"
            where = f"退出码 {ran.returncode}；命令 argv={' '.join(str(a) for a in (executable.name, 'components', 'run', '--component-dir', str(component_dir), '--payload', payload_arg))}"
            # 同时把 **完整 stdout/stderr** 打到日志里（`::group::` 折叠），
            # 不然每轮都要重新打 tag 才能看清原因 —— 太慢。
            print(f"::group::{cid} 完整输出 (stdout)")
            print((ran.stdout or "")[:4000])
            print("::endgroup::")
            print(f"::group::{cid} 完整输出 (stderr)")
            print((ran.stderr or "")[:4000])
            print("::endgroup::")
            report.failures.append((cid, "运行", f"{where}；输出尾部：{detail}"))
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
