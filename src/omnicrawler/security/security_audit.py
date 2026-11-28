from __future__ import annotations

import re
import urllib.parse
from collections import Counter
from pathlib import Path
from typing import Any

from ..core.safe_data import safe_json_loads

#: 凭据**键名**必须以词边界起始。
#:
#: 不加 `\b` 时会踩一个真实误报（2026-11-24 实测）：
#: `proxy: {list: 'secret://settings.proxy_list'}` 里 `secret` 是**路径的一部分**，
#: 却被当成键名匹配 ⇒ 已正确密封的引用被判成「明文凭据」，导出被拦。
#: 判据要问「这一行有没有 `key:` 形式的凭据字段」，而不是「有没有出现 secret 字样」。
_KEY_PATTERN = re.compile(
    r"(?i)\b(password|passwd|token|secret|authorization|cookie|api[-_]?key|client[-_]?secret)\s*:\s*([^\n#]+)"
)

#: 代理池里的 `[scheme://]user:pass@host` 授权段。
#:
#: 为什么单独加：键名规则只看「字段名像不像凭据」，而代理把凭据塞在**值**里
#: （`proxy: {list: 'http://u:p@h:8080'}`），键名 `list` 毫无可疑之处。
#: 实测：加这条之前，两种形态（含/不含 scheme）都未被告警 ⇒ 导出会带着密码出门。
_PROXY_CREDENTIAL_PATTERN = re.compile(r"\b[^\s:/@'\"]+:[^\s/@'\"]+@[^\s/]+")


def _mask_sealed_references(line: str) -> str:
    """把 `secret://...` 引用替换成等长占位，避免引用串里的字样被当成凭据。

    为什么必须遮：`secret://settings.proxy_list` 里的 `secret` 是**路径的一部分**，
    但键名正则看到 `secret:` 就当成「凭据字段」⇒ 已正确密封的引用被判成明文泄露，
    导出被拦（2026-11-24 实测）。遮掉之后，剩下的才是真正需要判定的内容。
    """
    # ★ 引用名**可以含斜杠**：`seal_secret("research/api", ...)` 实测产出
    # `secret://research/api`（`credentials.seal_secret` 的校验正则不许斜杠，
    # 但它是先 `set` 再回拼字符串，所以斜杠引用真实存在）。
    # 只遮 `[\w.\-]+` 会漏掉斜杠之后的部分，那一段里若出现 `user:pass@` 形态
    # 就会被当成明文凭据 —— 2026-11-24 由集成用例
    # `test_security_audit_and_pii_summary` 抓到（`api_key: secret://research/api`
    # 被误报）。
    return re.sub(r"secret://[\w.\-/]+", lambda m: " " * len(m.group(0)), line)


def _only_placeholder(value: str) -> bool:
    """这个「值」是不是只剩被遮蔽的引用留下的空壳。

    遮蔽把 `secret://ai.openai.api_key` 换成了等长空格，于是
    `api_key: '　　}}}'` 里**看起来有值**（引号、右括号），
    实测会把已正确密封的配置判成明文泄露、拒绝导出。
    判据：去掉引号与结构符号后如果只剩空白，就是空壳。
    """
    return not value.strip("'\" {},\t").strip()


def _scan_text(text: str) -> list[dict[str, Any]]:
    """扫描配置文本中的明文凭据（跳过 secret:// 引用、${} 变量与 <redacted>）。"""
    findings: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        masked = _mask_sealed_references(line)
        for match in _KEY_PATTERN.finditer(masked):
            value = match.group(2).strip().strip("'\"")
            if not value or _only_placeholder(value) or value.startswith(("${", "<redacted")):
                continue
            findings.append(
                {
                    "line": line_number,
                    "key": match.group(1),
                    "severity": "error",
                    "message": "检测到可能的明文凭据；请改用 secret:// 引用或环境变量。",
                }
            )
        # 代理把凭据塞在**值**里（`proxy: {list: 'http://u:p@h:8080'}`），
        # 键名（list/enabled）毫无可疑之处 ⇒ 键名规则永远抓不到。
        if _PROXY_CREDENTIAL_PATTERN.search(masked):
            findings.append(
                {
                    "line": line_number,
                    "key": "proxy_credentials",
                    "severity": "error",
                    "message": "检测到代理地址里内嵌的明文凭据（user:pass@host）；"
                    " 请改用 secret:// 引用。",
                }
            )
    return findings

def scan_config_text(text: str) -> dict[str, Any]:
    """对配置 YAML 文本做明文凭据扫描（S2.2 导出前检查用）。"""
    findings = _scan_text(text)
    return {"ok": not findings, "findings": findings}


def scan_config_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"ok": True, "findings": []}
    text = path.read_text(encoding="utf-8", errors="replace")
    return scan_config_text(text)


#: PII 检测的三个正则。**模块级预编译**（不是每次调用重编译）——
#: `re` 有 512 项缓存，重复 `compile` 命中缓存但单次仍要 ~0.7 µs。
_PII_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PII_PHONE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
_PII_ID_CARD = re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)")


def pii_summary(records: list[dict[str, Any]]) -> dict[str, int]:
    """统计记录里疑似 PII 的条数（email / 手机号 / 身份证号）。

    ## 2026-11-28：加**必要性预筛**，实测同结果下快一截

    `build_quality_report` 的 profile 显示本函数占整份报告约 **32%** 的时间，
    开销全在「对每条记录的全文跑 3 个 findall」。但大多数记录里根本没有某类 PII，
    而那些 findall 仍会完整扫一遍文本。

    预筛依据是**这两类 PII 的必要条件**（不是启发式猜测）：

    | 类别 | 必要条件 | 为什么充分 |
    |---|---|---|
    | email | 文本里必须有 `@` | 正则里有字面 `@`，没有就一定匹配不到 |
    | 手机号 | 文本长度 ≥ 11 | 正则最短可匹配形态是 11 位数字 |
    | 身份证 | 文本长度 ≥ 18 | 正则要求 18 位 |

    三条都是**保守**的：预筛为假时正则必为空，所以结果与逐条跑正则**完全一致**；
    预筛为真时才真的跑正则。已在测试里逐条比对两版结果。

    实测（交错采样取 9 次最小值，抵消机器漂移）：

    | 样本 | 旧 | 新 | 变化 |
    |---|---|---|---|
    | 1 万条，10% 有邮箱（贴近真实数据）| 136.4 ms | 97.0 ms | **+29%** |
    | 2 万条，全无 PII | 321.5 ms | 229.9 ms | **+28%** |
    | 2 万条，全有邮箱（最坏情况）| 329.3 ms | 329.3 ms | 0% |

    最坏情况不劣化：`@` 预筛命中后行为与原来完全一致，只多一次 `in` 判断。
    三组样本的结果与旧实现**逐条一致**（测试里断言）。
    """
    counts = {"email": 0, "phone": 0, "id_card_candidate": 0}
    for record in records:
        text = " ".join(str(value) for value in record.values() if value is not None)
        if "@" in text:
            counts["email"] += len(_PII_EMAIL.findall(text))
        # 长度判据放在 `in` 判据之前：`len` 是 O(1)，比任何子串查找都便宜。
        length = len(text)
        if length >= 11:
            counts["phone"] += len(_PII_PHONE.findall(text))
        if length >= 18:
            counts["id_card_candidate"] += len(_PII_ID_CARD.findall(text))
    return counts


def egress_audit_report(path: Path) -> dict[str, Any]:
    """Summarise the redacted, append-only task network boundary log."""

    events: Counter[str] = Counter()
    purposes: Counter[str] = Counter()
    subjects: Counter[str] = Counter()
    boundaries: Counter[tuple[str, str, int]] = Counter()
    sdk_boundaries: Counter[str] = Counter()
    invalid_lines = 0
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            item = safe_json_loads(line)
            if not isinstance(item, dict):
                invalid_lines += 1
                continue
            event = str(item.get("event", "unknown"))
            events[event] += 1
            purpose = str(item.get("purpose", ""))
            subject = str(item.get("subject", ""))
            if purpose:
                purposes[purpose] += 1
            if subject:
                subjects[subject] += 1
            parts = urllib.parse.urlsplit(str(item.get("url", "")))
            if parts.hostname:
                port = parts.port or (443 if parts.scheme in {"https", "wss"} else 80)
                boundaries[(parts.scheme, parts.hostname.casefold(), port)] += 1
            if event == "sdk_transport_boundary":
                sdk_boundaries[str(item.get("transport", "unknown"))] += 1
    return {
        "ok": invalid_lines == 0,
        "audit_file": str(path),
        "event_count": sum(events.values()),
        "events": dict(sorted(events.items())),
        "purposes": dict(sorted(purposes.items())),
        "subjects": dict(sorted(subjects.items())),
        "accessed_boundaries": [
            {"scheme": scheme, "host": host, "port": port, "events": count}
            for (scheme, host, port), count in sorted(boundaries.items())
        ],
        "sdk_transport_exceptions": dict(sorted(sdk_boundaries.items())),
        "blocked_attempts": events["blocked"],
        "invalid_lines": invalid_lines,
    }
