"""JSONPath 语法的**不支持项**登记与判定（叶子模块，2026-11-28）。

## 为什么单独拆出来

这份判定有两个消费者，而它们的依赖预算完全不同：

| 消费者 | 需要的东西 | 不能拉的依赖 |
|---|---|---|
| `extraction/extractors.py` | 判定 + 求值 | — |
| `core/config.py`（配置校验）| **只**判定 | bs4 / lxml / defusedxml |

把判定留在 `extractors.py` 里，配置校验就得为一个字符串检查拉进整个 DOM 解析栈；
而配置校验是 `validate` / GUI 加载 / 模板渲染的必经之路。

## 单一真源

`extractors.py` 从这里 **re-export**，所以两个消费者读的是同一份登记表 ——
不会出现「校验说行、运行时说不行」。
"""

from __future__ import annotations

#: 引擎**不支持**的 JSONPath 语法（静默当成 `.` 处理会得出**错误结果**）。
#:
#: ★ 2026-11-28 修：`$..author` 此前被分词成 `["author"]`（`..` 与 `.` 同样被跳过），
#:   于是它只命中**顶层** author，而不是递归命中所有层。用户看到非空结果，
#:   会以为递归搜索生效了 —— 这是**假阳性**，比返回空更糟。
UNSUPPORTED_JSON_PATH_SYNTAX: tuple[tuple[str, str], ...] = (
    ("..", "递归搜索 '..' 不受支持，请用 '.' 逐层导航（例如 $.data.items[*].title）"),
    ("[?(", "过滤条件 '[?(...)]' 不受支持，请改用字段提取后再筛选"),
)


def reject_unsupported_json_path(path: str) -> None:
    """对**引擎不支持**的 JSONPath 语法抛 ValueError（带修复提示）。

    为什么不静默降级：`$..author` 被当成 `$.author` 时返回的是**顶层那一个**，
    而非空。用户看到有结果就不会怀疑，于是整份数据少了几层 —— 这类缺陷
    在导出后才被发现，而那时已经重跑过。
    """
    for token, message in UNSUPPORTED_JSON_PATH_SYNTAX:
        if token in path:
            raise ValueError(f"JSONPath {path!r} 使用了不支持的语法：{message}")
