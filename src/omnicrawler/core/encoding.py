"""编码检测与容错解码（借鉴 textract 的 Unicode Sandwich 思路，不引入其 CLI 依赖）。

- ``detect_encoding``：chardet 检测编码；置信度低于阈值或缺失 chardet 时回退 fallback。
- ``smart_decode``：检测 → 解码（errors="replace" 容错坏码点）→ 返回 (文本, 实际编码)。
- ``read_text_auto``：读文件并自动解码，供 document_ir 的 .txt 解析等场景使用。

chardet 为纯 Python 可选依赖；缺失时透明回退 utf-8，不崩溃。
"""

from __future__ import annotations

from pathlib import Path

#: 检测置信度低于该值时回退 fallback（对齐 textract 的 0.80 阈值）
_DETECT_THRESHOLD = 0.80

#: B05-023：可接受的编码白名单——收窄 chardet 结果，只认常见文档编码，
#: 拒绝怪异别名/私有编码名，降低 decode 面。
_KNOWN_ENCODINGS = {
    "utf-8", "utf8", "utf-16", "utf-16le", "utf-16be",
    "gb18030", "gbk", "gb2312", "big5", "big5-hkscs",
    "shift-jis", "sjis", "euc-jp", "euc-kr",
    "latin-1", "latin1", "iso-8859-1", "windows-1252", "cp1252", "ascii",
}


def detect_encoding(data: bytes, *, fallback: str = "utf-8") -> str:
    """chardet 检测编码，置信度不足或无 chardet 时回退 fallback。"""
    try:
        import chardet
    except ImportError:  # pragma: no cover - 缺依赖环境
        return fallback
    result = chardet.detect(data)
    guess = (result or {}).get("encoding")
    confidence = float((result or {}).get("confidence") or 0.0)
    if not guess:
        return fallback
    if confidence < _DETECT_THRESHOLD:
        return fallback
    if guess.casefold().replace("_", "-") not in _KNOWN_ENCODINGS:
        return fallback
    try:
        # 校验编码名是否可被 Python 识别，避免 chardet 返回怪异别名
        data.decode(guess)
    except (LookupError, UnicodeDecodeError):
        return fallback
    return guess


def smart_decode(data: bytes, *, fallback: str = "utf-8") -> tuple[str, str]:
    """检测编码并容错解码。返回 (解码文本, 实际编码)。

    检测结果不可解码时，走兜底链 utf-8 → gb18030 → latin-1
    （对齐 ``extraction.extractors.decode_body`` 的候选顺序），
    最后以 errors="replace" 兜底，绝不崩溃。
    """
    encoding = detect_encoding(data, fallback=fallback)
    try:
        return data.decode(encoding), encoding
    except (LookupError, UnicodeDecodeError):
        pass
    for candidate in ("utf-8", "gb18030", "latin-1"):
        if candidate == encoding:
            continue
        try:
            return data.decode(candidate), candidate
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", errors="replace"), "utf-8"


def read_text_auto(path: Path, *, fallback: str = "utf-8") -> tuple[str, str]:
    """读文件并自动解码。返回 (文本, 实际编码)。"""
    data = path.read_bytes()
    return smart_decode(data, fallback=fallback)


__all__ = ["detect_encoding", "read_text_auto", "smart_decode"]
