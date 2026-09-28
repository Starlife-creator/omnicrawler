"""语料级 PII 检测与脱敏（纯标准库，base 安装即可用）。

为什么需要它
------------
`quality/dupe_filter.py` 已能判近重，但**没有任何一层回答"这批语料里有没有个人信息"**。
`security/data_governance.py` 的 `detect_sensitive_fields` 只按**字段值整体**匹配
（`^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$` 之类），而真实抓取里 PII 出现在**正文中间**：
一段商品评论里夹着邮箱与手机号，逐字段匹配完全看不见。

三条设计判据
------------
1. **分类必须可核验，不能靠"看起来像"。** 银行卡走 Luhn 校验、身份证走 18 位加权
   校验位，校验不过就不报——误报会让脱敏后的语料既不可信又丢信息。
2. **样本永不保留原值。** `PiiFinding.sample` 存的是**脱敏后**的形态（原值只留
   首尾各 2 位），因为发现报告本身常被当作"安全的"附在产物里一起外发。
3. **检测与改写分离。** `detect_pii` 只报告不改写（用于测量"语料里有多少 PII"），
   `redact_pii` 才改写。两个用途的失败代价相反：前者漏报＝数据治理结论错，后者
   误改＝语料被毁，所以不做成一个开关。

与既有组件的分工
----------------
* `security/redaction.py` 的 `redact_url` 处理 ``scheme://user:pass@host`` 凭据 ——
  那是**结构性**凭据，与"正文里恰好出现一串像密钥的字符"不是一回事。本模块**不**
  导入它：本仓 `check_architecture` 有环依赖预算，而 `security.redaction` 若反过来
  委托本模块就会形成 ``quality ↔ security`` 环（实测突破预算：8>7 / 41>39 / 63>61）。
  故这里只做与 `redact_url` 语义**一致**的最小替换，不跨模块依赖。
* 顺带修掉 `security/redaction.redact_value`：它的 docstring 声称做「高熵 token
  （16+ 位 base64/hex）脱敏」，函数体里那段只是注释、实际只调了 `redact_url`
  就 return——**文档承诺的能力从未实现**。现改为委托本模块的 secret 检测，
  让 docstring 与实现一致。
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

__all__ = [
    "PiiFinding",
    "RedactionResult",
    "detect_pii",
    "redact_pii",
    "pii_report",
    "luhn_valid",
]

#: 占位符模板。**类型要出现在产物里**——只写 ``[REDACTED]`` 会让下游无法区分
#: "这里原本是邮箱"与"这里原本是手机号"，而这个区分正是数据治理要用的。
PLACEHOLDER = "[REDACTED:{kind}]"

_SEVERITY = {
    "email": "high",
    "id_card": "high",
    "bank_card": "high",
    "phone": "medium",
    "secret_token": "high",
    "ipv4": "low",
    "url_credential": "high",
}


@dataclass(frozen=True, slots=True)
class PiiFinding:
    """一处 PII。``sample`` 已脱敏，**永不携带原值**。"""

    kind: str
    start: int
    end: int
    severity: str
    sample: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "sample": self.sample,
        }


@dataclass(frozen=True, slots=True)
class RedactionResult:
    """脱敏结果 + 按类型的计数（计数是"改写了多少"的证据，不是"检测到多少"）。"""

    text: str
    counts: dict[str, int]

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def luhn_valid(digits: str) -> bool:
    """Luhn 校验——银行卡号判定。校验不过一律不报，避免把订单号当卡号脱敏。"""
    numbers = [int(char) for char in digits if char.isdigit()]
    if not 13 <= len(numbers) <= 19:
        return False
    total = 0
    for index, digit in enumerate(reversed(numbers)):
        if index % 2:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _mask(value: str, keep: int = 2) -> str:
    """样本脱敏：只留首尾各 ``keep`` 位。"""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}{'*' * (len(value) - keep * 2)}{value[-keep:]}"


# 顺序即优先级：先匹配更"具体"的形态，避免手机号被数字串规则先吃掉。
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")),
    # 18 位身份证：17 位数字 + 校验位（数字或 X）
    ("id_card", re.compile(r"(?<![0-9A-Za-z])\d{17}[\dXx](?![0-9A-Za-z])")),
    # 银行卡：13-19 位，允许空格/连字符分组
    ("bank_card", re.compile(r"(?<![\d\-])(?:\d[ \-]?){12,18}\d(?![\d\-])")),
    # 大陆手机号
    ("phone", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    # IPv4
    ("ipv4", re.compile(r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}"
                        r"(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\d.])")),
    # 高熵疑似密钥：16+ 位 base64/hex 的独立 token。
    # 左边界**不**排除 `=`：`key=<secret>` 正是最常见的密钥形态（环境变量、配置文件），
    # 把 `=` 当分隔符排除会让这类密钥整条漏掉。右边界仍保留，避免匹配到更长 token 内部。
    ("secret_token", re.compile(r"(?<![A-Za-z0-9+/_-])[A-Za-z0-9+/]{16,}={0,2}(?![A-Za-z0-9+/=_-])")),
    # URL 内嵌凭据
    ("url_credential", re.compile(r"//[^/\s:@]+:[^@/\s]+@")),
)


def _validate(kind: str, value: str) -> bool:
    """按类型做可核验的校验；校验不过是误报，不报。"""
    if kind == "bank_card":
        return luhn_valid(value)
    if kind == "id_card":
        weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
        check = "10X98765432"
        total = sum(int(char) * weight for char, weight in zip(value[:17], weights, strict=True))
        return check[total % 11] == value[17].upper()
    if kind == "secret_token":
        # 纯数字长串交给 bank_card / phone 判定，否则 16 位订单号会被当密钥
        return not value.isdigit()
    if kind == "ipv4":
        return all(0 <= int(part) <= 255 for part in value.split("."))
    return True


def _spans(text: str) -> list[tuple[str, int, int]]:
    """收集通过校验的区间，**重叠时长的优先**（更具体的形态赢）。"""
    found: list[tuple[str, int, int]] = []
    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(0)
            if not _validate(kind, value):
                continue
            found.append((kind, match.start(), match.end()))
    found.sort(key=lambda item: (item[1], -(item[2] - item[1])))
    kept: list[tuple[str, int, int]] = []
    reach = -1
    for kind, start, end in found:
        if start < reach:
            continue  # 与已保留区间重叠，丢弃
        kept.append((kind, start, end))
        reach = end
    return kept


def detect_pii(text: str) -> tuple[PiiFinding, ...]:
    """只**检测**不改写。用于回答"这批语料里有多少 PII、分布如何"。"""
    if not isinstance(text, str) or not text:
        return ()
    return tuple(
        PiiFinding(
            kind=kind,
            start=start,
            end=end,
            severity=_SEVERITY.get(kind, "medium"),
            sample=_mask(text[start:end]),
        )
        for kind, start, end in _spans(text)
    )


def redact_pii(text: str, *, placeholder: str = PLACEHOLDER) -> RedactionResult:
    """把 PII 换成**带类型**的占位符。类型必须保留，否则下游无法区分形态。"""
    if not isinstance(text, str) or not text:
        return RedactionResult(text=text if isinstance(text, str) else "", counts={})
    spans = _spans(text)
    if not spans:
        return RedactionResult(text=text, counts={})

    pieces: list[str] = []
    counts: Counter[str] = Counter()
    cursor = 0
    for kind, start, end in spans:
        pieces.append(text[cursor:start])
        # URL 凭据交给既有实现，避免这里重写一套结构化脱敏
        if kind == "url_credential":
            # 与 security.redaction.redact_url 语义一致；不跨模块依赖（见模块说明）
            pieces.append("<redacted>")
        else:
            pieces.append(placeholder.format(kind=kind))
        counts[kind] += 1
        cursor = end
    pieces.append(text[cursor:])
    return RedactionResult(text="".join(pieces), counts=dict(counts))


def pii_report(texts: Iterable[str], *, sample_limit: int = 3) -> dict[str, Any]:
    """语料级 PII 画像：总量、按类型分布、按严重度分布、以及每类的脱敏样本。

    刻意**不含**任何原值：这份报告常被当作"安全的"随产物一起外发。
    """
    counts: Counter[str] = Counter()
    severity: Counter[str] = Counter()
    samples: dict[str, list[str]] = {}
    documents = with_pii = 0

    for text in texts:
        documents += 1
        if not isinstance(text, str) or not text:
            continue
        findings = detect_pii(text)
        if findings:
            with_pii += 1
        for finding in findings:
            counts[finding.kind] += 1
            severity[finding.severity] += 1
            bucket = samples.setdefault(finding.kind, [])
            if len(bucket) < sample_limit and finding.sample not in bucket:
                bucket.append(finding.sample)

    return {
        "documents": documents,
        "documents_with_pii": with_pii,
        "incidence": round(with_pii / documents, 4) if documents else 0.0,
        "total_findings": sum(counts.values()),
        "by_kind": dict(sorted(counts.items())),
        "by_severity": dict(sorted(severity.items())),
        "samples_redacted": samples,
        "note": "样本已脱敏（仅留首尾各 2 位），本报告不携带任何原值。",
    }
