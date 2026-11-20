"""组件冒烟：**判据由组件自己声明，宿主只负责执行与核对**。

## 为什么要这样

早先的验证脚本里有一堆硬编码的组件知识：

```python
if engine == "paddle": ...        # 我知道 paddle 用 paddle.Tensor 探针
if not runner: 数据组件跳过        # 我知道语言包不用跑
```

每加一个组件就得改验证脚本，不可持续。而且「组件能不能跑」是**组件的承诺**，
不是宿主的猜测。

所以改成：`recipe.json` 里写一段 `smoke`，验证器读它、执行它、核对它。
新组件**不需要改宿主代码**，市场侧也能照着跑（它不必知道 paddle 是什么）。

## 声明形态

```json
"smoke": {
  "payload": {"op": "ping"},     // 传给组件入口的 JSON（缺省 {}）
  "expect": ["ok"],               // 结果里必须存在的键，且值为真
  "skip_run": false,              // true = 纯数据组件，装完即完成，不执行
  "timeout_seconds": 300          // 可选，缺省 300
}
```

判据（缺一即红）：

1. `kind=wheel-pack` 必须有 `smoke`，且 `skip_run` 不为真；
2. `kind=data` 必须 `skip_run: true`（它没有可执行入口）；
3. `expect` 必须是非空列表 —— **空期望等于没验**（空集合会让断言恒真）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: `smoke` 允许的键。
ALLOWED_SMOKE_KEYS = frozenset(
    {"payload", "expect", "skip_run", "timeout_seconds", "description"}
)

#: 缺省超时（秒）。
DEFAULT_SMOKE_TIMEOUT = 300.0


class SmokeSpecError(ValueError):
    """`smoke` 声明不合法。"""


@dataclass(frozen=True, slots=True)
class SmokeSpec:
    """一次冒烟的执行规格。"""

    payload: dict[str, Any]
    expect: tuple[str, ...]
    skip_run: bool
    timeout_seconds: float
    description: str = ""


def parse_smoke(recipe: dict[str, Any]) -> SmokeSpec:
    """从配方里解析出 `SmokeSpec`，并按组件种类做自洽校验。"""
    from .component_recipe import component_kind

    kind = component_kind(recipe)
    smoke = recipe.get("smoke")

    if smoke is None:
        if kind == "data":
            # 数据组件没有可执行入口，允许省略 smoke（等价于 skip_run）
            return SmokeSpec(
                payload={}, expect=(), skip_run=True, timeout_seconds=DEFAULT_SMOKE_TIMEOUT
            )
        raise SmokeSpecError(
            f"kind={kind} 的组件必须声明 smoke —— "
            "「能不能跑」要由组件自己承诺，不能让验证器猜"
        )

    if not isinstance(smoke, dict):
        raise SmokeSpecError("smoke 必须是对象")

    unknown = set(smoke) - ALLOWED_SMOKE_KEYS
    if unknown:
        raise SmokeSpecError(f"smoke 含不允许的键：{sorted(unknown)}")

    payload = smoke.get("payload", {})
    if not isinstance(payload, dict):
        raise SmokeSpecError("smoke.payload 必须是对象")

    skip_run = bool(smoke.get("skip_run", False))

    expect_raw = smoke.get("expect", [])
    if not isinstance(expect_raw, list) or not all(
        isinstance(item, str) and item.strip() for item in expect_raw
    ):
        raise SmokeSpecError("smoke.expect 必须是字符串列表")
    expect = tuple(item.strip() for item in expect_raw)

    if not skip_run and not expect:
        # **空期望必须判红**：没有期望的冒烟等于没验，会让所有断言恒真。
        raise SmokeSpecError(
            "smoke.expect 不能为空 —— 空期望等于没验（空集合会让断言恒真）"
        )

    timeout = smoke.get("timeout_seconds", DEFAULT_SMOKE_TIMEOUT)
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        raise SmokeSpecError("smoke.timeout_seconds 必须是正数")

    # 与 kind 自洽
    if kind == "data" and not skip_run:
        raise SmokeSpecError("kind=data 的组件必须写 skip_run: true（它没有可执行入口）")
    if kind == "wheel-pack" and skip_run:
        raise SmokeSpecError(
            "kind=wheel-pack 的组件不能 skip_run —— 必须真的执行一次"
        )
    # native 与 wheel-pack 同属「有可执行入口」，必须真的跑一次。
    # 它走 `run_native_component`（exec 组件自带的二进制），
    # 协议同为 JSON-over-stdio，所以 `expect` 的判据完全通用。
    if kind == "native" and skip_run:
        raise SmokeSpecError(
            "kind=native 的组件不能 skip_run —— 必须真的执行一次"
        )

    return SmokeSpec(
        payload=payload,
        expect=expect,
        skip_run=skip_run,
        timeout_seconds=float(timeout),
        description=str(smoke.get("description") or ""),
    )


def verify_result(spec: SmokeSpec, result: dict[str, Any]) -> list[str]:
    """核对执行结果是否满足 `expect`。返回不满足项（空 = 通过）。"""
    problems: list[str] = []
    if not isinstance(result, dict):
        return [f"组件输出不是对象：{type(result).__name__}"]
    if result.get("ok") is False:
        stage = result.get("stage") or "worker"
        error = result.get("error") or "未提供错误信息"
        return [f"[{stage}] {error}"]
    for key in spec.expect:
        if key not in result:
            problems.append(f"结果缺少期望的键：{key}")
        elif not result[key]:
            problems.append(f"期望的键值为假：{key}={result[key]!r}")
    return problems
