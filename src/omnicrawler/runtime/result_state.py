from __future__ import annotations

from typing import Any


def succeeded_payload(value: Any) -> dict[str, Any]:
    """把一个**非 dict** 的运行结果包成终态 payload。

    S2.5.47：`ApplicationService.run()` 在旧版本/插件路径下可能返回非 dict
    （字符串、数字、列表……）。进程内后端（`InProcessBackend`）与独立 worker
    进程（`WorkerRuntime`）都必须把它落到终态，而不是一直停在 `running`。

    这段字面量原本在两处各写一遍，形状一旦漂移就会出现「一个后端说 succeeded、
    另一个还在 running」的分裂。收成一处，两处都调这里。
    """
    return {"status": "succeeded", "result": {"status": "succeeded", "value": value}}
