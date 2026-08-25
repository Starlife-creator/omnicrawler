"""抽取与智能采集域：field-suggest / record-actions / api-discover / timeline /
replay / auto-analyze / c4a-fetch / stealth-fingerprint / visual-select /
gen-templates。"""

from __future__ import annotations

import argparse


def configure(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    field_suggest = sub.add_parser("field-suggest", help="从保存的 HTML 自动推荐稳定字段选择器")
    field_suggest.add_argument("html")
    field_suggest.add_argument("--limit", type=int, default=100)
    field_suggest.add_argument("--output", "-o")
    recorder = sub.add_parser("record-actions", help="打开浏览器并录制点击、输入与滚动操作")
    recorder.add_argument("url")
    recorder.add_argument("--output", "-o", required=True)
    recorder.add_argument("--timeout", type=int, default=300)
    api = sub.add_parser("api-discover", help="从浏览器 API 捕获 JSON 生成 REST 模板")
    api.add_argument("input")
    api.add_argument("--output", "-o", required=True)
    timeline = sub.add_parser("timeline", help="查看证据胶囊时间线（run 内提取动作序列）")
    timeline.add_argument("--config", "-c", required=True)
    timeline.add_argument("--run", default="", help="run_id；省略时列出全部 run 的胶囊统计")
    timeline.add_argument("--capsule-dir", default=None, help="胶囊日志目录（默认 <workspace>/capsules）")
    timeline.add_argument("--limit", type=int, default=50, help="时间线条目上限")
    replay_cmd = sub.add_parser("replay", help="基于胶囊 + 归档 raw 限定重放字段提取")
    replay_cmd.add_argument("--config", "-c", required=True)
    replay_cmd.add_argument("--run", required=True, help="run_id")
    replay_cmd.add_argument("--field", required=True, help="要重放的字段名")
    replay_cmd.add_argument("--stage", default="extract", help="胶囊阶段（默认 extract）")
    replay_cmd.add_argument("--capsule-dir", default=None, help="胶囊日志目录（默认 <workspace>/capsules）")
    replay_cmd.add_argument("--timeout", type=float, default=10.0, help="重放子进程超时秒数")
    # 可视化选择器
    visual_sel = sub.add_parser("visual-select", help="启动浏览器可视化元素选择器 WebSocket 服务")
    visual_sel.add_argument("--port", type=int, default=8084, help="WebSocket 端口（默认 8084）")
    visual_sel.add_argument("--output", "-o", help="自动写入的 YAML 配置路径")
    # 智能爬虫
    auto_crawl = sub.add_parser("auto-analyze", help="智能分析页面结构，自动推断字段和分页")
    auto_crawl.add_argument("input", help="HTML 文件路径 或 URL")
    auto_crawl.add_argument("-o", "--output", help="输出 YAML 配置路径")
    auto_crawl.add_argument("--url", help="页面原始 URL")
    c4a = sub.add_parser("c4a-fetch", help="使用 Crawl4AI 进行轻量 JS 渲染抓取")
    c4a.add_argument("url", help="目标 URL")
    c4a.add_argument("--stealth", action="store_true", help="使用 undetected 浏览器模式")
    c4a.add_argument("--extract", help="CSS 提取 schema JSON 文件")
    c4a.add_argument("-o", "--output", help="输出 JSON 文件路径")
    # 反检测增强
    stealth_cmd = sub.add_parser("stealth-fingerprint", help="生成随机浏览器指纹（反检测）")
    stealth_cmd.add_argument("--count", type=int, default=1, help="生成数量")
    stealth_cmd.add_argument("--json", action="store_true", help="使用 JSON 输出")
    # Apify 模板生成
    tmpl_gen = sub.add_parser("gen-templates", help="根据 Apify 130+ 平台知识生成站点模板")
    tmpl_gen.add_argument("--list", action="store_true", help="列出所有已知平台")
    tmpl_gen.add_argument("--generate", metavar="PLATFORM", help="生成指定平台模板")
    tmpl_gen.add_argument("--all", metavar="DIR", help="生成所有平台模板到目录")
