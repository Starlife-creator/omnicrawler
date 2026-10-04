"""Preserve supported workflow semantics while parameterizing sensitive inputs."""
from __future__ import annotations

import copy
from typing import Any

from ..core.config import AppConfig


def extend_workflow(config: AppConfig, data: dict[str, Any]) -> dict[str, Any]:
    declarations: dict[str, Any] = {}
    def parameter(name: str, kind: str) -> str:
        declarations[name] = {"type": kind, "required": True}
        return "{{" + name + "}}"
    source = config.section("source")
    for key in ("pagination", "follow_xpath", "auth_check", "method", "content_type"):
        if source.get(key):
            data["source"][key] = copy.deepcopy(source[key])
    for key, kind in (("params", "object"), ("payload", "object"), ("query", "string"), ("variables", "object"), ("fields", "object")):
        if source.get(key) is not None and source.get(key) != {} and source.get(key) != "":
            data["source"][key] = parameter("source_" + key, kind)
    if source.get("query_file"):
        data["source"]["query"] = parameter("source_query", "string")
    if source.get("login"):
        raise ValueError("登录动作须由登录中心完成，不捕获内嵌登录配置")
    seeds = data["source"]["seeds"]
    for index, seed in enumerate(seeds):
        if not isinstance(seed, dict):
            continue
        if set(seed) - {"url", "method", "headers", "payload", "content_type", "kind", "render"}:
            raise ValueError("seed 包含不支持的请求属性，不能静默移除")
        if seed.get("headers"):
            seed["headers"] = parameter(f"seed_headers_{index + 1}", "object")
        else:
            seed.pop("headers", None)
        if seed.get("payload") is not None:
            seed["payload"] = parameter(f"seed_payload_{index + 1}", "object")
    for key in ("allow_patterns", "deny_patterns"):
        if config.section("crawl").get(key):
            data["crawl"][key] = copy.deepcopy(config.section("crawl")[key])
    download = config.section("download")
    if download.get("enabled"):
        if set(download) - {"enabled", "extensions", "media", "verified_pdf_manifest"}:
            raise ValueError("下载配置含未支持属性，不能静默移除")
        data["download"] = copy.deepcopy(download)
    browser = config.section("browser")
    if source["kind"] == "browser" or any(isinstance(seed, dict) and seed.get("render") for seed in seeds):
        allowed_browser = {"engine", "headless", "pool_size", "actions", "capture_api_responses", "max_api_response_bytes", "max_api_capture_bytes", "auto_generate_api_templates", "stealth_level"}
        if set(browser) - allowed_browser:
            raise ValueError("浏览器配置含未支持属性，不能静默移除")
        data["browser"] = copy.deepcopy(browser)
        actions = data["browser"].get("actions", [])
        for index, action in enumerate(actions):
            if not isinstance(action, dict) or set(action) - {"action", "selector", "selectors", "role", "name", "value", "key", "optional", "if_present", "timeout_ms", "times", "pause_ms"}:
                raise ValueError("动作包含未支持属性，不能静默移除")
            if "value" in action:
                action["value"] = parameter(f"action_value_{index + 1}", "string")
    authenticated = source.get("auth_check") or config.section("session").get("persist_cookies")
    if authenticated:
        data["session"] = {"persist_cookies": True, "name": parameter("session_name", "string"),
                           "bridge_to_http": config.section("session").get("bridge_to_http", True)}
    complex_task = source["kind"] in {"rest", "graphql", "form", "browser"} or authenticated
    if complex_task:
        for section in ("source", "http"):
            if config.section(section).get("headers"):
                data[section]["headers"] = parameter(section + "_headers", "object")
        data["egress"]["credential_domains"] = copy.deepcopy(config.section("egress").get("credential_domains", []))
    if config.raw.get("transformers"):
        raise ValueError("转换器扩展暂不捕获，请保留原始任务配置")
    updates = config.section("updates")
    data["updates"] = copy.deepcopy(updates)
    if config.section("extract").get("deduplicate_by"):
        data["extract"]["deduplicate_by"] = copy.deepcopy(config.section("extract")["deduplicate_by"])
    return declarations
