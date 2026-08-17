"""URL 脏数据清洗（借鉴 ahmadsalamifar/OmniCrawler 的 normalize_url 思路，按本项目规范重写）。

场景：从非结构化来源（微信聊天记录、Word 导出的文本、代码注释等）导入 URL 时，
常混有 CSV 分隔符粘连、括号/引号包裹、全角标点、不可见字符等脏数据。

原则：
- 只返回以 http(s):// 开头的可识别 URL；无法识别返回 None，不猜不补协议。
- 清洗是纯字符串操作，无网络、无副作用。
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

#: 不可见/控制字符（含零宽）；注意保留 \t（CSV 制表符分隔，用于截断）
_INVISIBLE_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200f\u2028-\u202f\ufeff]")
#: URL 开头的尾随垃圾：分隔符、括号、引号、全角/半角标点
_TRAILING_RE = re.compile(r"[,;:<>\[\]{}()\"'，。；：、】）】」』！？…\s]+$")
#: 文本中的 URL 粗匹配（用于 extract_urls_from_text）
_URL_RE = re.compile(r"https?://[^\s<>\"'，。；：、（）【】「」『』]+")

_VALID_SCHEME = ("http://", "https://")
#: 采集链路支持的协议（canonicalize_url / NetworkTargetPolicy 均只认 http/https）
_SUPPORTED_SCHEMES = ("http", "https")


def clean_url(raw: str | None) -> str | None:
    """清洗单个 URL；无法识别返回 None。

    处理：控制字符剔除 → 括号/引号/HTML 标签剥除 → 尾随标点剥离 → 分隔符截断。
    """
    if raw is None:
        return None
    text = _INVISIBLE_RE.sub("", raw).strip()
    if not text:
        return None
    # 剥外层 HTML 标签或尖括号包裹（<url>）
    text = text.strip("<>")
    # 剥成对括号包裹（(url) / "url" / 'url'）
    while text and text[0] in "(\"'[" and text[-1] in ")\"']":
        text = text[1:-1].strip()
    if not text:
        return None
    lower = text.casefold()
    # 只处理 http(s) URL；其余（ftp/无协议/邮箱等）不猜
    if not lower.startswith(_VALID_SCHEME):
        return None
    # 分隔符粘连：取到第一个 CSV/制表符分隔符为止
    cut = _split_marker(text)
    if cut > 0:
        text = text[:cut]
    # 尾随标点/括号剥离
    text = _TRAILING_RE.sub("", text).rstrip()
    if not text:
        return None
    return text


def clean_url_status(raw: str | None) -> tuple[str | None, str]:
    """清洗 URL 并返回状态原因；``clean_url`` 契约不变。

    供调用方做「非静默丢弃」：明确区分协议不受支持与无法识别，
    而不是笼统返回 None。

    Returns:
        ``(cleaned, status)``：
        - ``(url, "ok")``              清洗成功
        - ``(None, "unsupported:ftp")`` 协议明确但非 http(s)（采集链路不支持）
        - ``(None, "no_scheme")``      无协议/邮箱/纯路径，无法识别
        - ``(None, "invalid")``        空/清洗后无效/畸形 http(s)
    """
    cleaned = clean_url(raw)
    if cleaned is not None:
        return cleaned, "ok"
    if raw is None:
        return None, "invalid"
    text = _INVISIBLE_RE.sub("", raw).strip()
    if not text:
        return None, "invalid"
    # 用 urlparse 判定协议，而非正则猜 ://（javascript:void(0) 只有冒号无 //）
    parsed = urlparse(text)
    if parsed.scheme:
        if parsed.scheme.casefold() not in _SUPPORTED_SCHEMES:
            return None, f"unsupported:{parsed.scheme.casefold()}"
        # http(s) 但 clean_url 返回 None → 畸形（如缺 host），算 invalid
        return None, "invalid"
    return None, "no_scheme"


def _split_marker(text: str) -> int:
    """返回第一个分隔符位置；无则返回 0。只考虑 CSV/制表符/空白粘连。"""
    for index, char in enumerate(text):
        if char in ",;|\t，" and index > len("https://"):
            return index
    return 0


def extract_urls_from_text(text: str | None) -> list[str]:
    """从文本中提取并清洗全部 http(s) URL，顺序去重。"""
    if not text:
        return []
    out: list[str] = []
    for match in _URL_RE.finditer(text):
        cleaned = clean_url(match.group(0))
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out


__all__ = ["clean_url", "clean_url_status", "extract_urls_from_text"]
