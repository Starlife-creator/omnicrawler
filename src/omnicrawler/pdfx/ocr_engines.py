"""OCR 引擎注册表（条目 11）——**叶子模块**，不导入 pdfx 内任何其他模块。

为什么要单独一个模块
--------------------
引擎名单此前写死在**三处**（`ocr.create_backend`、`config.validate_runtime_config`、
`services.component_manager` 的 runtime.engine 白名单），而它们之间存在依赖关系：

    ocr.py   →  config.py        （顶层导入 ProjectConfig）
    config.py →  ocr.py          （若在这里查引擎名，就形成环）

把注册表提到本模块后，`config.py` 与 `component_manager.py` 都只依赖这个
**不导入任何 pdfx 模块**的叶子，环就消失了（架构门禁的 cycle budget 已验证）。

设计要点
--------
* **注册优先**：新增引擎调 :func:`register_engine`，不必改上面三处；
* **内置仍在**：paddle / tesseract 由 ``ocr.py`` 在底部注册，放开≠拆除；
* **可用性与注册分离**：``available`` 回答"本机能不能用"，与"是否已注册"是两件事；
* **未知引擎显式失败并枚举**：不静默回落，那会让用户以为跑的是他选的引擎。
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

#: 引擎名 → (工厂, 可用性回调)
_ENGINES: dict[str, tuple[Callable[[dict[str, Any]], Any], Callable[[], bool]]] = {}

#: 引擎名规范：小写字母/数字/`._-`，避免奇怪名字混进配置与日志
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

#: 内置引擎（**永远存在**，与写死时行为一致）。
#: 这份名单只用于"回落"和"给用户看的内置说明"，**不是**强制白名单。
BUILTIN_ENGINES: tuple[str, ...] = ("paddle", "tesseract")


def register_engine(
    name: str,
    factory: Callable[[dict[str, Any]], Any],
    *,
    available: Callable[[], bool] | None = None,
    replace: bool = False,
) -> None:
    """注册一个 OCR 引擎。

    :param name: 引擎名（小写字母/数字/`._-`），写进配置与组件清单。
    :param factory: 接收 ``config.ocr`` 字典，返回实现了 ``recognize`` /
        ``recognize_rich`` 的后端对象。
    :param available: 回答"本机现在能不能用"。缺省视为始终可用；
        组件式引擎应传一个检查运行时是否已安装的回调。
        **注意**：本模块无法用"函数名字符串"做延迟解析（那需要导入 ocr），
        所以调用方若想支持运行期替换，应传一个**每次调用都重新求值**的可调用对象，
        例如 ``lambda: ocr._paddle_ready()``。
    :param replace: 已注册时是否覆盖。默认**拒绝**——静默覆盖会让
        "谁把内置引擎换掉了"变成一个查不出来的问题。
    """
    key = str(name or "").strip().lower()
    if not _NAME_RE.match(key):
        raise ValueError(f"OCR 引擎名不合法: {name!r}（只允许小写字母/数字/._-）")
    if not callable(factory):
        raise ValueError("OCR 引擎工厂必须可调用")
    if key in _ENGINES and not replace:
        raise ValueError(f"OCR 引擎 {key!r} 已注册；如需覆盖请显式传 replace=True")
    _ENGINES[key] = (factory, available or (lambda: True))


def registered_engines() -> list[str]:
    """已注册的引擎名（稳定排序，便于错误信息与界面展示）。"""
    return sorted(_ENGINES)


def is_available(name: str) -> bool:
    """该引擎在本机是否可用。未注册一律视为不可用（不抛错，供探测用）。"""
    entry = _ENGINES.get(str(name or "").strip().lower())
    if entry is None:
        return False
    try:
        return bool(entry[1]())
    except Exception:  # noqa: BLE001 - 探测失败按不可用处理，不能拦住遍历
        return False


def resolve(name: str, config: dict[str, Any] | None = None) -> Any:
    """按名字实例化引擎；未注册时抛**可枚举**的错误。

    ``name`` 为 ``none`` / 空串时返回 ``None``（表示不启用）。
    """
    key = str(name or "").strip().lower()
    if key in {"", "none"}:
        return None
    entry = _ENGINES.get(key)
    if entry is None:
        known = ", ".join(registered_engines()) or "（无）"
        raise ValueError(f"未注册的OCR后端: {key}；已注册的引擎: {known}")
    return entry[0](config or {})


def engine_names_or_builtin() -> list[str]:
    """已注册引擎；注册表为空时回落到内置名单。

    **回落是保守的**：极早期导入阶段（ocr 还没注册完）宁可少放开一个名字，
    也不能因为"注册表暂时为空"把合法配置误判成非法。
    """
    names = registered_engines()
    return names if names else list(BUILTIN_ENGINES)


def unregister_for_tests(name: str) -> None:
    """仅供测试清理。

    **不对外暴露**（生产代码不该依赖它）：那会让"同一次运行里引擎集合变了"
    成为可能，而引擎身份正是依赖指纹的一部分。
    """
    _ENGINES.pop(str(name or "").strip().lower(), None)
