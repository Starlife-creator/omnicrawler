from __future__ import annotations

import re
from typing import Any

from ..core.config import AppConfig
from ..core.models import ExtractedRecord, FetchResult, ProcessResult
from ..core.safe_data import safe_json_loads, safe_regex_search
from ..core.utils import absolutize_resource_url
from .html_tools import node_attr, node_markup, node_text, parse_html, select_nodes

#: 「这个字段取的是**资源地址**」的确定性信号之一：**属性名**。
#: ★ 刻意**不用字段名**判断 —— 走查里就有 `链接文本: 2015`（锚文字），
#: 按名字把 `链接/地址` 一律当归一对象，会把 `2015` 拼成 `https://site/2015`。
_URL_ATTRS = frozenset({
    "href", "src", "data-src", "data-original", "data-url", "data-href", "data-image",
    "poster", "action", "formaction", "cite", "longdesc", "download",
})

#: 信号之二：**路径 / 属性键的叶子名**（JSON 字段路径、meta 属性名）。
_URL_KEY_NAMES = frozenset({
    "url", "uri", "link", "href", "src", "image", "images", "thumbnail", "picture",
    "photo", "avatar", "logo", "icon", "cover", "poster", "permalink", "canonical",
    "video", "audio", "attachment", "contenturl", "imageurl", "image_url", "video_url",
    "profile_image", "preview",
})


def _address_rule_kind(rule: Any, *, key_path: str = "") -> str:
    """这条规则的取值是不是「资源地址」？返回 ``""`` / ``"attr"`` / ``"key"``。

    ★ **`attr` 优先且互斥**：规则里写了 ``attr`` 就以它为准 —— 写了 ``attr: title``
    却因为字段路径叫 `image` 去当归一，那是**把两套信号混着用**，两边都不可信。
    """
    if isinstance(rule, dict):
        attr = str(rule.get("attr", "") or "").strip().casefold()
        if attr:
            return "attr" if attr in _URL_ATTRS else ""
    leaf = str(key_path).rsplit(".", 1)[-1].casefold() if key_path else ""
    return "key" if leaf in _URL_KEY_NAMES else ""


def _absolutize_value(value: Any, base_url: str) -> tuple[Any, Any]:
    """把取值补成绝对 URL；返回 ``(新值, 原值)``，**无改动时原值为 ``None``**。

    - 字符串：单值归一；
    - 列表（``all: true`` 或数组字段）：逐项归一，任一项变了才整体算"改过"；
    - 其它类型：原样返回。
    """
    if not base_url:
        return value, None
    if isinstance(value, list):
        rewritten = list(value)
        changed = False
        for index, item in enumerate(rewritten):
            absolute = absolutize_resource_url(base_url, item) if isinstance(item, str) else None
            if absolute and absolute != item:
                rewritten[index] = absolute
                changed = True
        return (rewritten, value) if changed else (value, None)
    if isinstance(value, str):
        absolute = absolutize_resource_url(base_url, value)
        if absolute and absolute != value:
            return absolute, value
    return value, None


def _with_absolutized_value(
    value: Any, trace: dict[str, Any], *, kind: str, base_url: str
) -> tuple[Any, dict[str, Any]]:
    """字段取值的**统一出口**：是资源地址就补成绝对 URL，并把**原值留在证据里**。

    ★ 归一放在**取值这一处**（`_apply_rule` / `_apply_xpath_rule` / `_structured_rule`
    都经此出口），而**不是**放在 `HTMLProcessor` —— `services/replay.py` 也会调
    `_apply_rule` 重放字段，只在 processor 里归一会让**重放值与运行值不一致**
    （"两处口径不许漂移"）。
    """
    if not kind or not base_url:
        return value, trace
    absolute, original = _absolutize_value(value, base_url)
    if original is None:
        return value, trace
    return absolute, {**trace, "clean_value": absolute, "absolutized_from": original}


def _apply_value_map(value: str, mapping: dict[str, Any]) -> tuple[Any, str | None]:
    """按规则的 ``value_map`` 把取值映射成目标值；返回 ``(新值, 原值或 None)``。

    匹配顺序：**整体值**优先（去首尾空白、大小写不敏感），其次**逐 token**
    —— ``attr: class`` 取到的是 ``"star-rating Three"``，值藏在其中一个 token 里。

    ★ **没命中就原样返回**（不猜、不丢）。页面出现词表外的取值时，用户看到的是原始值
      （据此可以扩表），而不是一个假数字或一列空值。
    ★ 映射结果**保留用户写的类型**（`{Three: 3}` 给的就是整数 3）——
      用户写的是数字就用数字，别替他降级成字符串。
    """
    text = value.strip()
    if not text:
        return value, None
    for key, mapped in mapping.items():
        if str(key).strip().casefold() == text.casefold():
            return mapped, value
    for token in text.split():
        for key, mapped in mapping.items():
            if str(key).strip().casefold() == token.casefold():
                return mapped, value
    return value, None


def _map_rule_values(values: list[Any], rule: Any) -> tuple[list[Any], list[str]]:
    """对整批取值套用 ``value_map``；返回 ``(新取值, 被改写的原值列表)``。"""
    mapping = rule.get("value_map") if isinstance(rule, dict) else None
    if not isinstance(mapping, dict) or not mapping:
        return values, []
    mapped_values: list[Any] = []
    origins: list[str] = []
    for item in values:
        if isinstance(item, str):
            mapped, original = _apply_value_map(item, mapping)
            if original is not None:
                origins.append(original)
                mapped_values.append(mapped)
                continue
        mapped_values.append(item)
    return mapped_values, origins


def decode_body(result: FetchResult) -> str:
    content_type = result.headers.get("content-type", "")
    charset_match = re.search(r"charset=([\w.-]+)", content_type, flags=re.I)
    candidates = [charset_match.group(1)] if charset_match else []
    candidates.extend(["utf-8", "gb18030", "latin-1"])
    for charset in candidates:
        try:
            return result.body.decode(charset)
        except (UnicodeDecodeError, LookupError):
            continue
    return result.body.decode("utf-8", errors="replace")


def _structured_metadata(document: Any, result: FetchResult) -> dict[str, Any]:
    jsonld: list[Any] = []
    for node in select_nodes(document, 'script[type="application/ld+json"]'):
        raw = node_text(node).strip()
        if not raw:
            continue
        value = safe_json_loads(raw)
        if value is None:
            continue
        if isinstance(value, list):
            jsonld.extend(value)
        else:
            jsonld.append(value)

    open_graph: dict[str, str] = {}
    twitter: dict[str, str] = {}
    meta: dict[str, str] = {}
    for node in select_nodes(document, "meta[property], meta[name]"):
        key = (node_attr(node, "property") or node_attr(node, "name") or "").strip()
        value = node_attr(node, "content")
        if not key or value is None:
            continue
        lowered = key.casefold()
        meta[key] = value
        meta[lowered] = value
        if lowered.startswith("og:"):
            open_graph[lowered[3:]] = value
        elif lowered.startswith("twitter:"):
            twitter[lowered[8:]] = value
    browser_responses = result.meta.get("api_responses", [])
    if not isinstance(browser_responses, list):
        browser_responses = []
    return {
        "jsonld": jsonld, "open_graph": open_graph, "twitter": twitter, "meta": meta,
        "browser_responses": browser_responses,
    }


def _structured_rule(
    field_name: str,
    rule: Any,
    structured: dict[str, Any],
    *,
    base_url: str = "",
) -> tuple[bool, Any, dict[str, Any]]:
    if not isinstance(rule, dict):
        return False, None, {}
    source = str(rule.get("source", "")).casefold().replace("-", "_")
    if rule.get("jsonld_path") is not None:
        source = "jsonld"
    if source == "jsonld":
        path = str(rule.get("jsonld_path", rule.get("path", field_name)))
        jsonld_values: list[Any] = []
        for item in structured["jsonld"]:
            jsonld_values.extend(json_path(item, path))
        value: Any = jsonld_values if rule.get("all") else (
            jsonld_values[0] if jsonld_values else rule.get("default")
        )
        jsonld_trace = {
            "source": "jsonld", "path": path, "matches": len(jsonld_values),
            "raw_value": jsonld_values[0] if jsonld_values else None, "clean_value": value,
            "rule": dict(rule), "confidence": 1.0 if jsonld_values else 0.0,
        }
        value, jsonld_trace = _with_absolutized_value(
            value, jsonld_trace, kind=_address_rule_kind(rule, key_path=path), base_url=base_url
        )
        return True, value, jsonld_trace
    if source in {"opengraph", "open_graph", "twitter", "meta"}:
        bucket = "open_graph" if source in {"opengraph", "open_graph"} else source
        key = str(rule.get("property", rule.get("path", field_name)))
        normalized = key.casefold()
        if bucket == "open_graph" and normalized.startswith("og:"):
            normalized = normalized[3:]
        if bucket == "twitter" and normalized.startswith("twitter:"):
            normalized = normalized[8:]
        mapping = structured[bucket]
        value = mapping.get(normalized) if bucket != "meta" else (
            mapping.get(key) or mapping.get(normalized)
        )
        if value is None:
            value = rule.get("default")
        meta_trace = {
            "source": bucket, "path": key, "matches": int(value is not None),
            "raw_value": value, "clean_value": value, "rule": dict(rule),
            "confidence": 1.0 if value is not None else 0.0,
        }
        # 这里的 `key` 就是**取值时用的那个 meta 键**（未显式声明 property/path 时才退回字段名）
        # —— 取值来源本身就带地址语义，不是"按字段名猜"。
        value, meta_trace = _with_absolutized_value(
            value, meta_trace, kind=_address_rule_kind(rule, key_path=key), base_url=base_url
        )
        return True, value, meta_trace
    if source in {"browser_response", "network_response", "api_response"}:
        path = str(rule.get("path", "$"))
        url_pattern = str(rule.get("url_pattern", ""))
        response_values: list[Any] = []
        sources: list[str] = []
        for response in structured["browser_responses"]:
            if not isinstance(response, dict):
                continue
            response_url = str(response.get("url", ""))
            if url_pattern and not re.search(url_pattern, response_url):
                continue
            payload = response.get("json", response.get("text"))
            if payload is None:
                continue
            matched = json_path(payload, path) if not isinstance(payload, str) else (
                [payload] if path in {"", "$", "."} else []
            )
            response_values.extend(matched)
            sources.extend([response_url] * len(matched))
        value = response_values if rule.get("all") else (
            response_values[0] if response_values else rule.get("default")
        )
        response_trace = {
            "source": "browser_response", "path": path, "response_url": sources[0] if sources else None,
            "matches": len(response_values), "raw_value": response_values[0] if response_values else None,
            "clean_value": value, "rule": dict(rule), "confidence": 1.0 if response_values else 0.0,
        }
        value, response_trace = _with_absolutized_value(
            value, response_trace, kind=_address_rule_kind(rule, key_path=path), base_url=base_url
        )
        return True, value, response_trace
    return False, None, {}


def _apply_xpath_rule(
    context: Any, rule: dict[str, Any], *, base_url: str = ""
) -> tuple[Any, dict[str, Any]]:
    try:
        from lxml import html as lxml_html
    except ImportError as exc:
        raise RuntimeError(
            "XPath extraction requires lxml; install omnicrawler-platform[html]"
        ) from exc
    xpath = str(rule.get("xpath", ""))
    if len(xpath) > 4000:
        raise ValueError("XPath 表达式过长，拒绝执行以防止 DoS")
    root = lxml_html.fromstring(node_markup(context))
    nodes = root.xpath(xpath)
    values: list[str] = []
    raw_values: list[str] = []
    for node in nodes:
        if hasattr(node, "get") and rule.get("attr"):
            node_value = node.get(str(rule["attr"]))
        elif hasattr(node, "text_content"):
            node_value = node.text_content()
        else:
            node_value = node
        if node_value is None:
            continue
        raw_values.append(str(node_value)[:2000])
        cleaned = str(node_value).strip()
        regex = rule.get("regex")
        if regex:
            # S2.5.14：safe_regex_search 防病态正则卡死；group 越界防护
            match = safe_regex_search(str(regex), cleaned, flags=re.S)
            if not match:
                continue
            group = rule.get("group", "value" if "value" in match.groupdict() else 1 if match.lastindex else 0)
            try:
                cleaned = str(match.group(group)).strip()
            except (IndexError, re.error):
                continue
        for transform in rule.get("transforms", []):
            name = str(transform).casefold()
            if name == "normalize_space":
                cleaned = re.sub(r"\s+", " ", cleaned).strip()
            elif name == "lower":
                cleaned = cleaned.lower()
            elif name == "upper":
                cleaned = cleaned.upper()
            elif name == "strip":
                cleaned = cleaned.strip()
        if cleaned:
            values.append(cleaned)
    values, mapped_origins = _map_rule_values(values, rule)
    if rule.get("all"):
        result_value: Any = (
            str(rule.get("join", " | ")).join(values) if rule.get("join") is not None else values
        )
    else:
        result_value = values[0] if values else rule.get("default")
    trace = {
        "xpath": xpath, "matches": len(nodes), "raw_value": raw_values[0] if raw_values else None,
        "clean_value": result_value, "rule": dict(rule), "confidence": 1.0 if values else 0.0,
    }
    if mapped_origins:
        trace["mapped_from"] = (
            mapped_origins[0] if len(mapped_origins) == 1 else list(mapped_origins)
        )
    return _with_absolutized_value(
        result_value, trace, kind=_address_rule_kind(rule), base_url=base_url
    )


def _apply_rule(
    context: Any, rule: Any, *, base_url: str = ""
) -> tuple[Any, dict[str, Any]]:
    if isinstance(rule, str):
        rule = {"selector": rule}
    if not isinstance(rule, dict):
        return rule, {}
    if rule.get("xpath"):
        return _apply_xpath_rule(context, rule, base_url=base_url)
    candidates = rule.get("selectors")
    if isinstance(candidates, list):
        traces: list[dict[str, Any]] = []
        for index, candidate in enumerate(candidates):
            candidate_rule = dict(rule)
            candidate_rule.pop("selectors", None)
            if isinstance(candidate, str):
                candidate_rule["selector"] = candidate
            elif isinstance(candidate, dict):
                candidate_rule.update(candidate)
            else:
                continue
            value, trace = _apply_rule(context, candidate_rule, base_url=base_url)
            traces.append(trace)
            if value is not None and value != "" and value != []:
                return value, {"candidate": index, "attempts": traces, **trace}
        return rule.get("default"), {"candidate": None, "attempts": traces}
    selector = str(rule.get("selector", ""))
    nodes = select_nodes(context, selector) if selector else [context]
    values: list[Any] = []
    raw_values: list[str] = []
    for node in nodes:
        value = node_attr(node, str(rule["attr"])) if rule.get("attr") else node_text(node)
        if value is None:
            continue
        raw_values.append(str(value)[:2000])
        regex = rule.get("regex")
        if regex:
            # S2.5.14：safe_regex_search 防病态正则卡死；group 越界防护
            match = safe_regex_search(str(regex), str(value), flags=re.S)
            if not match:
                continue
            group = rule.get("group", "value" if "value" in match.groupdict() else 1 if match.lastindex else 0)
            try:
                value = match.group(group)
            except (IndexError, re.error):
                continue
        value = str(value).strip()
        for transform in rule.get("transforms", []):
            name = str(transform).lower()
            if name == "strip":
                value = value.strip()
            elif name == "normalize_space":
                value = re.sub(r"\s+", " ", value).strip()
            elif name == "lower":
                value = value.lower()
            elif name == "upper":
                value = value.upper()
        if value != "":
            values.append(value)

    # 走查 R3.6：值写在 class 名里时取到的是 `star-rating Three` ⇒ 由 value_map 翻成数字。
    # 放在 transform 之后、选择之前，逐值映射。
    values, mapped_origins = _map_rule_values(values, rule)

    if rule.get("all"):
        value = str(rule.get("join", " | ")).join(map(str, values)) if rule.get("join") is not None else values
    else:
        value = values[0] if values else rule.get("default")
    trace = {
        "selector": selector,
        "matches": len(nodes),
        "raw_value": raw_values[0] if raw_values else None,
        "clean_value": value,
        "rule": {
            key: rule[key]
            for key in ("selector", "attr", "regex", "group", "transforms", "default", "all", "join")
            if key in rule
        },
        "confidence": 1.0 if values else 0.0,
    }
    if mapped_origins:
        # ★ 被映射过的值必须**可见**（与 `absolutized_from` 同一条纪律）：
        #   `Three` 变成 `3` 之后，用户仍要能看出这个数字是从哪来的。
        trace["mapped_from"] = (
            mapped_origins[0] if len(mapped_origins) == 1 else list(mapped_origins)
        )
    return _with_absolutized_value(
        value, trace, kind=_address_rule_kind(rule), base_url=base_url
    )


class HTMLProcessor:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def process(self, result: FetchResult) -> ProcessResult:
        text = decode_body(result)
        document = parse_html(text)
        structured = _structured_metadata(document, result)
        extract = self.config.section("extract")
        item_selector = str(extract.get("item_selector", ""))
        items = select_nodes(document, item_selector) if item_selector else [document]
        fields = extract.get("fields", {})
        records: list[ExtractedRecord] = []
        for index, item in enumerate(items, 1):
            data: dict[str, Any] = {}
            evidence: dict[str, Any] = {}
            if fields:
                for name, rule in fields.items():
                    handled, value, trace = _structured_rule(
                        str(name), rule, structured, base_url=result.final_url
                    )
                    if not handled:
                        value, trace = _apply_rule(item, rule, base_url=result.final_url)
                    if value is not None:
                        data[str(name)] = value
                    evidence[str(name)] = {"source_url": result.final_url, **trace}
            else:
                title_nodes = select_nodes(document, "title")
                heading_nodes = select_nodes(document, "h1")
                data = {
                    "url": result.final_url,
                    "title": node_text(title_nodes[0]) if title_nodes else "",
                    "heading": node_text(heading_nodes[0]) if heading_nodes else "",
                    "text": node_text(item)[:5000],
                }
                if structured["jsonld"]:
                    data["structured_data"] = structured["jsonld"]
                if structured["open_graph"]:
                    data["open_graph"] = structured["open_graph"]
                if structured["twitter"]:
                    data["twitter"] = structured["twitter"]
                if structured["browser_responses"]:
                    data["browser_api_responses"] = structured["browser_responses"]
            if data:
                records.append(ExtractedRecord(result.final_url, "html_item", data, {"item": index, **evidence}))
        return ProcessResult(records=records)


_PATH_TOKEN = re.compile(r"([^.[\]]+)|\[(\*|\d+)\]")


def json_path(value: Any, path: str) -> list[Any]:
    if path in {"", "$", "."}:
        return [value]
    tokens = [a or b for a, b in _PATH_TOKEN.findall(path.lstrip("$."))]
    current = [value]
    for token in tokens:
        next_values: list[Any] = []
        for item in current:
            if token == "*" and isinstance(item, list):
                next_values.extend(item)
            elif str(token).isdigit() and isinstance(item, list):
                index = int(token)
                if 0 <= index < len(item):
                    next_values.append(item[index])
            elif isinstance(item, dict) and token in item:
                next_values.append(item[token])
        current = next_values
    return current


def json_field_values(item: Any, name: Any, rule: Any) -> tuple[str, list[Any]]:
    """按 JSONProcessor 的候选路径顺序返回首个有值的字段匹配。"""
    candidates = rule.get("paths") if isinstance(rule, dict) else None
    paths = (
        [str(value) for value in candidates]
        if isinstance(candidates, list) and candidates
        else [str(rule.get("path", name)) if isinstance(rule, dict) else str(rule)]
    )
    selected = paths[0]
    values: list[Any] = []
    for candidate in paths:
        values = json_path(item, candidate)
        if values:
            selected = candidate
            break
    return selected, values


class JSONProcessor:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def process(self, result: FetchResult) -> ProcessResult:
        payload = safe_json_loads(decode_body(result))
        if payload is None:
            # S2.5.14：错误带上 URL 上下文，便于定位具体响应
            raise ValueError(f"响应体不是合法 JSON，无法按 JSON 模式提取（{result.final_url}）")
        extract = self.config.section("extract")
        items = json_path(payload, str(extract.get("item_path", "$")))
        fields = extract.get("fields", {})
        records: list[ExtractedRecord] = []
        for index, item in enumerate(items, 1):
            if fields:
                data: dict[str, Any] = {}
                evidence: dict[str, Any] = {}
                for name, rule in fields.items():
                    path, values = json_field_values(item, name, rule)
                    value: Any = values if isinstance(rule, dict) and rule.get("all") else (values[0] if values else None)
                    entry = {
                        "source_url": result.final_url,
                        "path": path,
                        "matches": len(values),
                        "raw_value": values[0] if values else None,
                        "clean_value": value,
                        "rule": dict(rule) if isinstance(rule, dict) else {"path": str(rule)},
                        "confidence": 1.0 if values else 0.0,
                    }
                    # 走查 R4.3：JSON 侧的"地址"信号是**路径叶子名** —— 键名是载荷给的，
                    # 不是从用户可见的字段名猜的（后者会把「链接文本」也当成地址）。
                    value, entry = _with_absolutized_value(
                        value,
                        entry,
                        kind=_address_rule_kind(rule, key_path=path),
                        base_url=result.final_url,
                    )
                    if value is not None:
                        data[str(name)] = value
                    evidence[str(name)] = entry
            else:
                data = item if isinstance(item, dict) else {"value": item}
                evidence = {"path": extract.get("item_path", "$")}
            records.append(ExtractedRecord(result.final_url, "json_item", data, {"item": index, **evidence}))
        return ProcessResult(records=records)


class TextProcessor:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def process(self, result: FetchResult) -> ProcessResult:
        return ProcessResult(records=[ExtractedRecord(
            result.final_url,
            "text",
            {"url": result.final_url, "content_type": result.content_type, "text": decode_body(result)[:200_000]},
        )])


class TableProcessor:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def process(self, result: FetchResult) -> ProcessResult:
        document = parse_html(decode_body(result))
        selector = str(self.config.section("extract").get("table_selector", "table"))
        # S2.5.32：extract.fields 参与列选择/过滤，不再被忽略
        fields = self.config.section("extract").get("fields", {})
        records: list[ExtractedRecord] = []
        for table_index, table in enumerate(select_nodes(document, selector), 1):
            rows = select_nodes(table, "tr")
            if not rows:
                continue
            first = select_nodes(rows[0], "th, td")
            headers = [node_text(cell).strip() or f"column_{index}" for index, cell in enumerate(first, 1)]
            has_header = bool(select_nodes(rows[0], "th"))
            data_rows = rows[1:] if has_header else rows
            if not has_header:
                headers = [f"column_{index}" for index in range(1, len(first) + 1)]
            for row_index, row in enumerate(data_rows, 1):
                if fields:
                    data = _extract_table_fields(row, headers, fields)
                    if not data:
                        continue
                else:
                    values = [node_text(cell) for cell in select_nodes(row, "th, td")]
                    if not values:
                        continue
                    data = {headers[index] if index < len(headers) else f"column_{index + 1}": value for index, value in enumerate(values)}
                records.append(ExtractedRecord(
                    result.final_url, "html_table_row", data,
                    {"table": table_index, "row": row_index, "selector": selector},
                ))
        return ProcessResult(records=records)


def _extract_table_fields(row: Any, headers: list[str], fields: dict[str, Any]) -> dict[str, Any]:
    """S2.5.32：按 extract.fields 规则从表格行提取字段。

    规则支持：``{"column": "表头名" | int 索引}`` 或 ``{"selector": "td.x"}``；
    字符串简写视为表头名。
    """
    cells = select_nodes(row, "th, td")
    data: dict[str, Any] = {}
    for name, rule in fields.items():
        column = rule if isinstance(rule, str) else (rule or {}).get("column")
        target: list[Any] = []
        if column is None:
            selector = (rule or {}).get("selector")
            if isinstance(selector, str) and selector:
                target = select_nodes(row, selector)
        elif isinstance(column, int):
            if 0 <= column < len(cells):
                target = [cells[column]]
        else:
            wanted = str(column).strip().casefold()
            index = next(
                (i for i, header in enumerate(headers) if str(header).strip().casefold() == wanted),
                None,
            )
            if index is not None and index < len(cells):
                target = [cells[index]]
        value = " ".join(node_text(cell).strip() for cell in target).strip() if target else None
        if value:
            data[str(name)] = value
    return data


def choose_processor(result: FetchResult) -> str:
    content_type = result.content_type
    prefix = result.body[:256].lstrip().lower()
    if "json" in content_type or result.final_url.lower().endswith(".json"):
        return "json"
    if prefix.startswith((b"{", b"[")):
        return "json"
    if "html" in content_type or result.final_url.lower().endswith((".html", ".htm", "/")):
        return "html"
    if prefix.startswith((b"<!doctype html", b"<html")):
        return "html"
    if content_type.startswith("text/") or "xml" in content_type:
        return "text"
    return "binary"


def register(registry) -> None:
    registry.register_processor("html", HTMLProcessor)
    registry.register_processor("json", JSONProcessor)
    registry.register_processor("text", TextProcessor)
    registry.register_processor("table", TableProcessor)
