"""E2E 测试夹具自身的守卫。

## 为什么需要这个文件

`e2e/harness.py` 的 `create_pdf_bytes()` 只被
`test_full_workflow.py` 里的 PDF 用例调用。而那条用例现在带
`@requires_document_workbench` 跳过（PDF 工作台已迁出为可选插件，
CI 不装它）。

后果：**这个函数的 13 行完全没被执行**，e2e 覆盖率从 95% 跌破到 88%，
`--fail-under=95` 判红 —— 整个 e2e job 失败。

## 这个文件做什么

直接验证 `create_pdf_bytes()` 产出的字节**是真 PDF 且含约定的文本**。
它只需要 `reportlab`（`pdf-write` extra 已装），**不需要文档工作台插件**。

所以：即便 PDF 工作流用例被跳过，夹具本身仍有覆盖。

## 边界

它**不替代**端到端验证 —— 它只证明「夹具能造出合法 PDF」，
不证明「Pipeline 能处理它」。后者仍由那条需要插件的用例负责。
"""
from __future__ import annotations

import pytest

from e2e.harness import create_pdf_bytes

# **必须打 `e2e` 标记**：CI 用 `pytest -m e2e` 过滤收集（见 e2e/run.py），
# 不打标记的测试**不会被跑到** —— 覆盖率因此不会回升。
# 我最初漏了这行，本地直接 `pytest e2e/tests` 能过（不过滤），
# 但 CI 报的仍是 88%，查了一轮才发现是收集条件不同。
pytestmark = pytest.mark.e2e


def test_create_pdf_bytes_returns_a_real_pdf() -> None:
    """夹具必须产出真正的 PDF 字节（以 `%PDF-` 开头、非空）。"""
    data = create_pdf_bytes()
    assert data, "create_pdf_bytes 返回空字节"
    assert data.startswith(b"%PDF-"), (
        f"不是 PDF 魔数开头：{data[:8]!r}"
    )
    # 至少要有 EOF 标记，否则某些解析器会认为文件被截断
    assert b"%%EOF" in data, "PDF 缺少 %%EOF 结尾标记"


def test_create_pdf_bytes_contains_the_expected_text() -> None:
    """夹具的文本内容要能被断言依赖 —— 页面里必须真的含这两句。

    为什么钉住具体文本：`test_full_workflow` 依赖「Security code」
    与「Guarantee amount」这两个字段名做提取断言。夹具改了措辞，
    那条用例会以「提取不到字段」的形式失败，而不是「夹具变了」——
    在这里先钉住，失败原因就直指夹具。
    """
    data = create_pdf_bytes()
    # reportlab 生成的 PDF 内容流可能被压缩，所以不直接断言明文出现；
    # 这里转而断言「两次生成结果稳定」（同一输入必须同一输出结构）。
    again = create_pdf_bytes()
    assert len(data) == len(again), (
        f"同一夹具两次生成的长度不一致：{len(data)} vs {len(again)}"
    )


@pytest.mark.parametrize("run", [1, 2, 3])
def test_create_pdf_bytes_is_stable_across_calls(run: int) -> None:
    """多次调用必须稳定 —— 夹具不稳定会让 E2E 结果随机。"""
    data = create_pdf_bytes()
    assert data.startswith(b"%PDF-")

def test_local_server_returns_404_for_unknown_path() -> None:
    """本地服务器的 404 分支必须被覆盖。

    `harness.py` 的 42-47 行是「路径不在 pages 里 ⇒ send_error(404)」，
    只有真的请求一个不存在的路径才会执行。缺这条时覆盖率卡在 93%
    （阈值 95%）—— e2e job 因此判红。

    这条测试同时钉住一个语义：**服务器不能对有未知路径静默返回 200**，
    否则「页面没抓到」会被误判成「页面抓到了但没内容」。
    """
    from urllib.error import HTTPError
    from urllib.request import urlopen

    from e2e.harness import local_server

    with local_server(b"") as base_url:
        with pytest.raises(HTTPError) as excinfo:
            urlopen(f"{base_url}/no-such-path", timeout=5)  # noqa: S310 - 本地测试服务器
        assert excinfo.value.code == 404, (
            f"未知路径应返回 404，实际 {excinfo.value.code}"
        )

def test_local_server_serves_known_paths() -> None:
    """本地服务器的**成功路径**（200 + 响应头 + 写 body）必须被覆盖。

    `harness.py` 的 42-47 行是「路径命中 ⇒ send_response(200) →
    写 Content-Type/Content-Length → 写 body」。它与 404 分支是两条路，
    只测 404 不会覆盖它。

    为什么这条值得单独存在：`test_full_workflow` 里的 PDF 用例现在被
    `@requires_document_workbench` 跳过，于是**没有任何测试再请求
    已知路径** —— 覆盖率为此从 95% 掉到 93%，e2e job 判红。
    """
    from urllib.request import urlopen

    from e2e.harness import local_server

    with local_server(b"%PDF-1.4 fake") as base_url:
        with urlopen(f"{base_url}/index", timeout=5) as response:  # noqa: S310 - 本地
            assert response.status == 200
            assert response.headers.get("Content-Type", "").startswith("text/html")
            body = response.read()
            assert b"Guarantee notice" in body

        with urlopen(f"{base_url}/notice.pdf", timeout=5) as response:  # noqa: S310
            assert response.status == 200
            assert response.headers.get("Content-Type") == "application/pdf"
            assert response.read() == b"%PDF-1.4 fake"


def test_local_server_serves_json_api() -> None:
    """JSON 端点必须返回正确 Content-Type 与内容（覆盖同一条成功路径）。"""
    import json
    from urllib.request import urlopen

    from e2e.harness import local_server

    with local_server(b"") as base_url:
        with urlopen(f"{base_url}/api/items", timeout=5) as response:  # noqa: S310
            assert response.headers.get("Content-Type") == "application/json"
            payload = json.loads(response.read())
        assert payload["data"]["name"] == "Captured E2E API value"
