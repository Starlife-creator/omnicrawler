"""`location: body` + 非映射 `payload`：**启动前**就要报错（CLI 与 GUI 共用入口）。

判据真源＝`core.pagination.body_location_payload_error`（定义一处），本文件测的是
`core/config.validate_config` **接进来之后**的行为：

* `location: body` 且 payload 是**非映射真值**（如字符串 / 数字）⇒ 明确报错；
* ★ **规则必须只对 body 生效**：`location: query`（缺省）配同样的字符串 payload
  **不许**报错 —— 字符串 payload 在别处（如 `text/plain` / 表单体）是合法写法。
  这条用**差分断言**表达（body 的错误列表 == query 的错误列表），比逐个断言更抗噪声。
* 空值（缺省 / `null`）一律当"没有请求体" ⇒ 不报错。
  ★ 其中 `payload: null` 是**本轮的第二个崩溃点**：`get("payload", {})` 在键存在而值为
  `None` 时返回 `None`，旧代码 `dict(None)` 直接 `TypeError` —— 现在它与"缺省"等价。
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from omnicrawler.core.config import AppConfig, ConfigParseError, load_config, validate_config

_BASE: dict = {
    "project": {"name": "t", "workspace": "work"},
    "source": {"kind": "rest", "seeds": [{"url": "https://api.example.com/", "method": "POST"}]},
}

_QUERY = {"type": "page", "parameter": "p", "location": "query"}
_BODY = {"type": "page", "parameter": "p", "location": "body"}


def _errors(*, pagination: dict, payload: object = None, with_payload: bool = False) -> list[str]:
    """返回**全部**校验错误（不过滤；差分断言会把无关基线错误抵消掉）。"""
    raw = copy.deepcopy(_BASE)
    source = raw["source"]
    source["pagination"] = pagination
    if with_payload:
        source["payload"] = payload
    config = AppConfig(path=Path("p.yaml"), root=Path("."), raw=raw, workspace=Path("work"))
    errors, _warnings = validate_config(config)
    return errors


_BASELINE = _errors(pagination=_QUERY)


def test_string_payload_is_rejected_in_body_location() -> None:
    errors = _errors(pagination=_BODY, payload="raw-body-text", with_payload=True)
    mine = [e for e in errors if "source.payload" in e]
    assert mine, errors
    assert "str" in mine[0], "要说清实际类型"


def test_mapping_payload_adds_no_error() -> None:
    assert _errors(pagination=_BODY, payload={"size": 10}, with_payload=True) == _BASELINE


def test_absent_and_null_payload_add_no_error() -> None:
    """缺省与显式 `null` 都当"没有请求体"（`null` 此前会在运行时抛裸 `TypeError`）。"""
    assert _errors(pagination=_BODY) == _BASELINE
    assert _errors(pagination=_BODY, payload=None, with_payload=True) == _BASELINE


def test_rule_is_conditional_on_body_location() -> None:
    """★ 关键的反向半条：`query`（缺省）配**同样的**字符串 payload **不许**被拦。

    字符串 payload 在非 body 场景是合法写法（`text/plain` / 表单体）⇒ 判据若写成
    "payload 必须是映射"这种**无条件**规则，就会误伤这一大片合法配置。
    """
    assert _errors(pagination=_QUERY, payload="raw-body-text", with_payload=True) == _BASELINE


@pytest.mark.parametrize("value", ["raw-body-text", 42, True], ids=["str", "int", "bool"])
def test_load_config_raises_with_readable_reason(tmp_path: Path, value: object) -> None:
    """端到端：走**共用入口** `load_config` 时，用户看到的是可行动的中文原因。"""
    path = tmp_path / "bad.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "project": {"name": "t", "workspace": str(tmp_path / "w")},
                "source": {
                    "kind": "rest",
                    "seeds": [{"url": "https://api.example.com/", "method": "POST"}],
                    "pagination": _BODY,
                    "payload": value,
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigParseError) as caught:
        load_config(path)
    assert "source.payload" in str(caught.value), str(caught.value)


def test_load_config_accepts_null_payload(tmp_path: Path) -> None:
    """`payload:`（显式 null）**不再**崩：与"缺省"等价。"""
    path = tmp_path / "null.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "project": {"name": "t", "workspace": str(tmp_path / "w")},
                "source": {
                    "kind": "rest",
                    "seeds": [{"url": "https://api.example.com/", "method": "POST"}],
                    "pagination": _BODY,
                    "payload": None,
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.section("source").get("payload") is None
