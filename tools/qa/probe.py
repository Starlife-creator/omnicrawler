"""诊断探针：**不改源码**就能观察某个函数的每次调用。

## 这个工具解决什么

本会话有一条测试我最终没查清：`test_component_transactions` 的验签失败。
我排除了签名规范化、路径、密钥字节、模块覆盖、测试污染，最后定位到
「`inspect_package` 被调用两次，第二次拿到的公钥不同」——**然后就停住了**。

停住的原因不是能力不够，而是**诊断成本太高**：要看调用细节，只能

1. 打开源文件加 `print(..., file=sys.stderr)`
2. 跑一次测试（可能几十秒）
3. 看完再删掉 print、确认字节还原

三步一轮，每轮只回答一个小问题。**改源码做诊断本身还有风险**：
忘了删、或者删的时候顺手改坏了别的地方。

## 这个工具怎么做

用 `sys.settrace` 的思路换成更简单的做法：**包装目标函数**（monkey-patch），
在调用点打印参数、返回值、异常。可以连续观察多次调用，看清
「第二次为什么不一样」。

探针脚本本体在 `tools/qa/probe_template.py`（独立文件，见那里的说明），
本工具只负责填参数、起子进程。

## 用法

```powershell
python tools/qa/probe.py `
    --target omnicrawler.services.component_manager:ComponentManager.inspect_package `
    --test tests/unit/services/test_component_transactions.py::test_xxx
```

## 边界（如实说明）

* **只支持 Python 层函数**：C 扩展（如 `hashlib` 内部）无法包装。
* **有性能开销**：每次调用都打印，跑全量测试会很慢 —— 目标是「看清行为」，
  不是跑基准。
* **不改变行为**：只读参数与返回值，不修改、不注入。
  若观察到某次调用触发了别的路径，那是真实行为，不是探针造成的。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

OK_MARK = "[OK]"
BAD_MARK = "[FAIL]"

_ROOT = Path(__file__).resolve().parents[2]

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

#: 模板文件（与工具同目录）。
_TEMPLATE = Path(__file__).resolve().parent / "probe_template.py"


def _parse_target(spec: str) -> tuple[str, str]:
    """把 `module:Class.method` 拆成 `(模块名, 属性路径)`。"""
    module, _, attribute = spec.partition(":")
    if not module or not attribute:
        raise ValueError(
            f"target 格式应为 `module:Attr` 或 `module:Class.method`，收到 {spec!r}"
        )
    return module, attribute


def render_probe(targets: list[str], test_args: list[str]) -> str:
    """把模板里的两个占位符替换成实际参数。"""
    template = _TEMPLATE.read_text(encoding="utf-8")
    return (
        template.replace("__TARGETS__", repr(targets))
        .replace("__TEST_ARGS__", repr(test_args))
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="不改源码，观察某函数的每次调用（参数/返回/异常）",
    )
    parser.add_argument(
        "--target",
        action="append",
        required=True,
        help="目标函数，格式 `module:Attr` 或 `module:Class.method`，可重复",
    )
    parser.add_argument(
        "--test",
        required=True,
        help="传给 pytest 的目标（文件或 `文件::用例`）",
    )
    parser.add_argument("--timeout", type=int, default=600, help="超时秒数（默认 600）")
    args = parser.parse_args(argv)

    if not _TEMPLATE.is_file():
        print(f"{BAD_MARK} 模板缺失：{_TEMPLATE}")
        return 2

    for spec in args.target:
        try:
            _parse_target(spec)
        except ValueError as exc:
            print(f"{BAD_MARK} {exc}")
            return 2

    script_path = _ROOT / ".probe_tmp.py"
    script_path.write_text(render_probe(args.target, [args.test]), encoding="utf-8")

    try:
        from tools.child_env import utf8_env

        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(_ROOT / "src"), str(_ROOT), env.get("PYTHONPATH", "")]
        ).strip(os.pathsep)
        env.update(utf8_env())

        print(f"探针目标：{', '.join(args.target)}")
        print(f"测试目标：{args.test}")
        print("─" * 60)
        result = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=args.timeout,
            check=False,
        )
        print("─" * 60)
        print(f"退出码 {result.returncode}")
    except subprocess.TimeoutExpired:
        print(f"{BAD_MARK} 探针超时（{args.timeout} 秒）")
        return 1
    finally:
        script_path.unlink(missing_ok=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
