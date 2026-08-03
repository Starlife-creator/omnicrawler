"""EasySpider JSON 任务 → OmniCrawler YAML 配置 / Task IR 转换器。

用法:
    python -m omnicrawl.easyspider_bridge task.json -o config.yaml

CLI:
    omnicrawl import-easyspider task.json -o config.yaml
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from ..core.utils import user_agent

# ── EasySpider graph node type/option 常量 ──────────────────────────────
_ROOT = -1
_OP = 0       # 操作节点
_FLOW = 1     # 流程控制节点

# option 值含义（操作节点 type:0）— 基于 EasySpider 实际 JSON 任务验证
_OP_OPEN = 1       # 打开网页
_OP_CLICK = 2      # 点击元素
_OP_EXTRACT = 3    # 提取数据
_OP_INPUT = 4      # 输入文本
_OP_SCROLL = 5     # 滚动（部分版本）

# option 值含义（流程控制节点 type:1）
_FLOW_LOOP = 8     # 循环
_FLOW_COND = 9     # 条件判断（部分版本）


def _resolve_xpath(params: dict[str, Any]) -> str:
    """从 EasySpider 的 params 中提取最佳 XPath。"""
    xpath = params.get("xpath", "")
    if not xpath:
        all_xpaths = params.get("allXPaths", "")
        if isinstance(all_xpaths, str) and all_xpaths:
            xpath = all_xpaths
        elif isinstance(all_xpaths, list) and all_xpaths:
            # 优先选最短的 XPath（通常最通用）
            xpath = min((str(p) for p in all_xpaths if str(p)), key=len, default="")
    return xpath


def _sanitize_filename(name: str) -> str:
    """净化文件名。"""
    return re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]", "_", name).strip("_") or "easyspider_task"


class EasySpiderImporter:
    """将 EasySpider JSON 任务转换为 OmniCrawler 配置。"""

    def __init__(self, json_path: str | Path) -> None:
        with open(json_path, encoding="utf-8-sig") as fh:
            self._raw: dict[str, Any] = json.load(fh)
        self._graph: list[dict[str, Any]] = self._raw.get("graph", [])
        self._node_index: dict[int, dict[str, Any]] = {
            node["id"]: node for node in self._graph
        }
        # -- 用 parentId + position 重建父子关系（避免 id:-1 重复） --
        self._children: dict[int, list[int]] = {}
        for idx, node in enumerate(self._graph):
            pid = node.get("parentId", 0)
            # 跳过自引用（parentId == id 的根节点）
            if pid != node.get("id", -2):
                self._children.setdefault(pid, []).append(idx)
        for children in self._children.values():
            children.sort(key=lambda i: self._graph[i].get("position", 0))
        self._output_params: list[dict[str, Any]] = self._raw.get("outputParameters", [])
        self._input_params: list[dict[str, Any]] = self._raw.get("inputParameters", [])
        self._seen_fields: list[dict[str, Any]] = []
        self._actions: list[dict[str, Any]] = []
        self._has_browser: bool = False

    # ── 主入口 ──────────────────────────────────────────────────────────

    def to_config(self) -> dict[str, Any]:
        """生成 OmniCrawler 完整 YAML 配置。"""
        config: dict[str, Any] = {}
        config["project"] = {"name": _sanitize_filename(self._raw.get("name", "easyspider_task"))}
        config["source"] = self._build_source()
        config["crawl"] = self._build_crawl()
        config["http"] = {"user_agent": user_agent("+bot"), "respect_robots": True}
        config["extract"] = self._build_extract()
        config["outputs"] = self._build_outputs()
        if self._has_browser:
            config["browser"] = {"engine": "playwright", "headless": True, "actions": self._actions}
            if not self._actions:
                del config["browser"]["actions"]
        return config

    def to_task_ir(self) -> dict[str, Any]:
        """生成 OmniCrawler Task IR（中间表示）。"""
        config = self.to_config()
        ir: dict[str, Any] = {
            "ir_version": 1,
            "identity": {"name": config["project"]["name"], "origin": "easyspider"},
            "goal": {"description": self._raw.get("desc", "")},
            "source": config["source"],
            "actions": self._actions,
            "fields": config.get("extract", {}).get("fields", {}),
            "outputs": config.get("outputs", {}),
            "capabilities": [],
            "extensions": {"easyspider_raw": self._raw.get("name", ""), "easyspider_version": self._raw.get("version", "")},
        }
        if self._has_browser:
            ir["capabilities"].append("browser")
        return ir

    # ── 构建各部分 ──────────────────────────────────────────────────────

    def _build_source(self) -> dict[str, Any]:
        urls = self._raw.get("url", "")
        links = self._raw.get("links", "") or urls
        seeds = [u.strip() for u in links.replace("\n", " ").split() if u.strip()]
        # 从输入参数补充 URL
        for param in self._input_params:
            if param.get("nodeName") == "打开网页" and param.get("value"):
                val = str(param["value"]).replace("\\n", "\n")
                for line in val.split("\n"):
                    line = line.strip()
                    if line and line not in seeds:
                        seeds.append(line)
        if not seeds:
            # 从 graph 第一个打开网页节点获取
            for node in self._graph:
                if node.get("type") == _OP and node.get("option") == _OP_OPEN:
                    u = (node.get("parameters", {}).get("url") or
                         node.get("parameters", {}).get("links", ""))
                    if u:
                        for line in str(u).replace("\\n", "\n").split("\n"):
                            line = line.strip()
                            if line and line not in seeds:
                                seeds.append(line)
        if not seeds:
            raise ValueError("无法从 EasySpider 任务中解析出任何入口 URL")
        return {"kind": "browser" if self._has_browser else "url_list", "seeds": seeds}

    def _build_crawl(self) -> dict[str, Any]:
        # 检测是否有翻页循环 — 从根节点出发，按 parentId 查找
        has_pagination = False
        for child_idx in self._children.get(0, []):
            child = self._graph[child_idx]
            if child.get("type") == _FLOW and child.get("title") == "循环" and not child.get("isInLoop"):
                has_pagination = True
                break
        result: dict[str, Any] = {"max_pages": 200}
        if has_pagination:
            result["strategy"] = "bfs"
        return result

    def _build_extract(self) -> dict[str, Any]:
        self._seen_fields = []
        self._actions = []
        self._has_browser = False
        # 从根节点（索引 0）开始遍历 graph 数组
        self._walk(0, is_in_loop=False)
        fields: dict[str, Any] = {}
        for item in self._seen_fields:
            name = item["name"]
            field_spec: dict[str, Any] = {}
            if item.get("selector"):
                field_spec["selector"] = item["selector"]
            if item.get("attribute"):
                field_spec["attribute"] = item["attribute"]
            if item.get("type"):
                field_spec["type"] = item["type"]
            if item.get("desc"):
                field_spec["desc"] = item["desc"]
            if item.get("exampleValues"):
                examples = item["exampleValues"]
                if isinstance(examples, list) and examples:
                    field_spec["examples"] = [e.get("value", "") for e in examples[:5]
                                               if isinstance(e, dict)]
            fields[name] = field_spec
        return {"mode": "html", "fields": fields}

    def _build_outputs(self) -> dict[str, Any]:
        fmt = self._raw.get("outputFormat", "csv")
        result: dict[str, Any] = {"jsonl": True}
        if fmt == "csv":
            result["csv"] = True
        if fmt in ("xlsx", "excel"):
            result["xlsx"] = True
        if fmt == "json":
            result["jsonl"] = True
        return result

    # ── 图遍历 ──────────────────────────────────────────────────────────

    def _walk(self, graph_idx: int, *, is_in_loop: bool, _depth: int = 0) -> None:
        """按 graph 数组索引遍历（处理 id:-1 重复问题）。"""
        if _depth > 50 or graph_idx < 0 or graph_idx >= len(self._graph):
            return
        node = self._graph[graph_idx]
        ntype = node.get("type", -1)
        option = node.get("option", 0)
        params = node.get("parameters", {})
        title = node.get("title", "")

        if ntype == _OP:
            self._handle_operation(option, params, title, is_in_loop)

        # 按 parentId 找到子节点并递归
        node_id = node.get("id", -1)
        child_indices = self._children.get(node_id, [])
        for child_idx in child_indices:
            self._graph[child_idx]
            child_in_loop = is_in_loop or (ntype == _FLOW and option == _FLOW_LOOP)
            self._walk(child_idx, is_in_loop=child_in_loop, _depth=_depth + 1)

    def _handle_operation(self, option: int, params: dict[str, Any], title: str, in_loop: bool) -> None:
        xpath = _resolve_xpath(params)

        if option == _OP_OPEN:
            # 打开网页 — 已由 source.seeds 覆盖
            wait = params.get("maxWaitTime", 10)
            if wait and wait != 10:
                self._actions.append({"action": "wait_ms", "value": wait * 1000})

        elif option == _OP_CLICK:
            self._has_browser = True
            if xpath:
                action: dict[str, Any] = {"action": "click", "selector": xpath}
                wait = params.get("wait", 2)
                if wait:
                    action["timeout_ms"] = wait * 1000
                self._actions.append(action)

        elif option == _OP_EXTRACT:
            sub_params = params.get("params", [])
            for sp in sub_params:
                if not isinstance(sp, dict):
                    continue
                rel_xpath = sp.get("relativeXPath", "")
                all_xpaths = sp.get("allXPaths", "")
                if isinstance(all_xpaths, list) and all_xpaths:
                    sel = min((str(p) for p in all_xpaths if str(p)), key=len, default=rel_xpath)
                elif isinstance(all_xpaths, str) and all_xpaths:
                    sel = all_xpaths
                else:
                    sel = rel_xpath or xpath
                field: dict[str, Any] = {
                    "name": sp.get("name", sp.get("desc", f"字段_{len(self._seen_fields)}")),
                    "selector": sel,
                }
                if sp.get("contentType") == 0:  # text
                    field["attribute"] = "text"
                else:  # link/other
                    field["attribute"] = "href"
                examples = sp.get("exampleValues")
                if examples:
                    field["exampleValues"] = examples
                if sp.get("desc"):
                    field["desc"] = sp["desc"]
                self._seen_fields.append(field)

        elif option == _OP_SCROLL:
            self._has_browser = True
            scroll_type = params.get("scrollType", 0)
            if scroll_type == 0:
                self._actions.append({"action": "scroll_bottom"})
            else:
                count = params.get("scrollCount", 1)
                self._actions.append({"action": "scroll", "value": str(count)})

        elif option == _OP_INPUT:
            self._has_browser = True
            value = params.get("value", "")
            if xpath and value:
                self._actions.append({"action": "fill", "selector": xpath, "value": value})


def import_easyspider(json_path: str, output_path: str | None = None) -> dict[str, Any]:
    """导入 EasySpider JSON，返回配置 dict；若提供 output_path 则写入 YAML。"""
    importer = EasySpiderImporter(json_path)
    config = importer.to_config()
    if output_path:
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write("# Generated from EasySpider task by omnicrawl import-easyspider\n")
            yaml.dump(config, fh, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return config


# ── CLI 入口 ────────────────────────────────────────────────────────────
def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="EasySpider JSON → OmniCrawler 配置转换器")
    parser.add_argument("json", help="EasySpider 任务 JSON 文件路径")
    parser.add_argument("-o", "--output", help="输出 YAML 文件路径（默认 stdout）")
    parser.add_argument("--ir", action="store_true", help="输出 Task IR JSON 而非 YAML 配置")
    args = parser.parse_args()

    importer = EasySpiderImporter(args.json)
    if args.ir:
        ir = importer.to_task_ir()
        output = json.dumps(ir, ensure_ascii=False, indent=2)
    else:
        config = importer.to_config()
        output = yaml.dump(config, allow_unicode=True, default_flow_style=False, sort_keys=False)

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"已写入: {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    main()
