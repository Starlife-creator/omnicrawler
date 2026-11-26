"""裸域名 / 动作词 / 追问 —— 清单式真实需求暴露的三处缺口。

## 由来

用户给了一份 100 条站点清单，里面的需求写法全是**裸域名**：

    抓取 books.toscrape.com 的书名、价格和评分，输出 CSV

而解析器只认 `https?://`。于是这条需求被判成「没有 URL」⇒ 落到 `ambiguous`
分支 ⇒ URL 变成 `file:///placeholder`，**字段与输出格式全部丢失，warnings 为空**。
26 条清单式需求里 24 条是这种写法 —— 这不是边缘情况，是主路径。
"""

from __future__ import annotations

import pytest

from omnicrawler.commands.task import compile_request
from omnicrawler.services import natural_language_task as nlt
from omnicrawler.services.natural_language_task import compile_natural_language

# ── ① 裸域名必须被认成 URL ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("demand", "host"),
    [
        ("抓取 books.toscrape.com 的书名、价格", "https://books.toscrape.com"),
        ("抓取 news.qscrape.dev 的标题和作者", "https://news.qscrape.dev"),
        ("抓取 web-scraping.dev/pagination 的商品", "https://web-scraping.dev/pagination"),
    ],
)
def test_bare_domain_is_recognised(demand: str, host: str) -> None:
    """不带协议的域名要补上 https，而不是当成"没有 URL"。"""
    assert compile_natural_language(demand).task.url == host


def test_bare_domain_does_not_lose_the_rest_of_the_request() -> None:
    """★ 这是真正的代价：URL 判错会让**字段和输出格式一起被丢掉**。

    实测：`抓取 books.toscrape.com 的书名、价格和评分，输出 CSV` 在修之前
    解析出 `fields=[]`、`formats=[xlsx,csv,jsonl]`。
    """
    task = compile_natural_language(
        "抓取 books.toscrape.com 的书名、价格和评分，输出 CSV"
    ).task
    assert task.fields == ("书名", "价格", "评分")
    assert task.output_formats == ("csv",)


@pytest.mark.parametrize("text", ["data.csv 帮我转换一下", "看看 README.md 里的说明"])
def test_file_names_are_not_mistaken_for_domains(text: str) -> None:
    """补裸域名识别不能把 `.csv` / `.md` 当成主机名。"""
    match = nlt._URL.search(text)
    assert match is None, f"{text!r} 被误判成 URL：{match.group(0) if match else ''}"


def test_explicit_scheme_is_left_untouched() -> None:
    """已经写了协议的不许再拼一个 https://。"""
    assert compile_natural_language(
        "抓取 http://example.org/a 的标题"
    ).task.url == "http://example.org/a"


# ── ② 动作词不是字段名 ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("demand", "forbidden"),
    [
        ("抓取 webscraper.io 的商品名称和价格，要翻页", "翻页"),
        ("抓取 quotes.toscrape.com/scroll 的语录和作者，滚动加载", "滚动加载"),
        ("抓取 example.org 的标题，抓完再加载更多", "加载更多"),
    ],
)
def test_action_words_are_not_field_names(demand: str, forbidden: str) -> None:
    """用户写「要翻页」是在说**怎么抓**，不是要抓一列叫「翻页」的东西。

    实测：确认单上曾出现「字段内容：名称、价格、要翻页」——用户看到会当场失去信任。
    """
    fields = compile_request(demand)["task"]["fields"]
    assert forbidden not in "".join(fields)


# ── ③ 没有 URL 时必须**说出来**，不能静默给占位地址 ────────────────────────


def test_request_without_any_url_says_so() -> None:
    """★ 修之前：`mode=ambiguous`、URL=`file:///placeholder`、**warnings 为空**。

    用户拿到一份看起来正常、实际上指向假地址的确认单。CLI 上还是一条
    「成功」的 JSON。这不是"少一个字段"，是**让用户以为任务已经配置好了**。
    """
    draft = compile_natural_language("随便抓点什么")
    assert draft.mode == "ambiguous"
    assert draft.task.warnings, "没有 URL 时必须给出可执行的提醒，不能只靠 mode 字段"
    # ★ 判据要写**具体缺什么**，不能只写「提到地址」。
    #   我第一版写成 `"URL" in joined or "地址" in joined`，反向断言当场判定它是
    #   **假守卫**：同一份 warnings 里第二条本来就含「地址」（它在说占位符的事），
    #   于是把第一条整句删掉，断言照样绿。
    #   第二版改成 `"网址" in joined or "URL" in joined` —— **仍然假**：
    #   第二条本来就含「URL」（它在说占位符不能直接跑）。同一个 warnings 里
    #   "含某个词"根本区分不出"有没有那句话"。
    #   最终判据：**必须有一条以「需求里没有找到网址」开头的提醒**。
    assert any(w.startswith("需求里没有找到网址") for w in draft.task.warnings), (
        f"必须明确指出「没有找到网址」，实际：{draft.task.warnings}"
    )
