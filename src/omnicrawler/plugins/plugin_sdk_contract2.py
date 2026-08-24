"""契约 2 脚手架模板与校验（Phase 3：plugins scaffold-contract2）。

方案第 38/67 轮：新建契约 2 工程骨架——handle 入口 + PLUGIN_METADATA/
plugin.yaml 双通道字段对齐 + 契约测试入口。业务逻辑由作者填充。
"""

from __future__ import annotations

import re

_ID = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")


def validate_contract2_id(plugin_id: str) -> None:
    if not _ID.fullmatch(plugin_id):
        raise ValueError(
            "插件 ID 必须以小写字母开头，仅含小写字母、数字、下划线或短横线"
        )


def build_plugin_py(*, plugin_id: str, display_name: str, version: str) -> str:
    """契约 2 plugin.py 骨架：PLUGIN_METADATA 静态字面量 + handle 入口。"""
    return (
        f'"""契约 2 插件：{display_name}（subprocess 隔离运行）。"""\n'
        "\n"
        "PLUGIN_METADATA = {\n"
        f"    \"name\": \"{plugin_id}\",\n"
        f"    \"version\": \"{version}\",\n"
        "    \"description\": \"示例契约 2 插件：请在 handle 中实现业务逻辑\",\n"
        "    \"plugin_types\": [\"source\"],\n"
        "    \"permissions\": [],\n"
        '    "dependencies": [],\n'
        '    "license": "MIT",\n'
        '    "execution_mode": "subprocess",\n'
        "}\n"
        "\n"
        "\n"
        "def handle(operation, payload):\n"
        "    \"\"\"契约 2 统一入口：operation = 'source.seed' 等；payload 为 dict。\"\"\"\n"
        "    if operation == \"source.seed\":\n"
        '        return {"requests": [{"url": "https://example.com/"}]}\n'
        "    # 未知操作：返回 dict（协议不变式），宿主忽略\n"
        "    return {\"operation\": operation}\n"
    )


def build_test_conftest(*, plugin_id: str) -> str:
    """契约测试入口：继承公共 Contract2Suite（F1 本地绿 = CI 绿）。"""
    return (
        "from pathlib import Path\n"
        "\n"
        "import pytest\n"
        "\n"
        "from omnicrawler.plugins.plugin_contract_suite import Contract2Suite\n"
        "\n"
        "\n"
        "class TestContract(Contract2Suite):\n"
        "    @pytest.fixture(scope=\"class\")\n"
        "    @staticmethod\n"
        "    def contract_plugin_dir():\n"
        "        return Path(__file__).resolve().parents[1]\n"
    )


# plugin.yaml 骨架：与 PLUGIN_METADATA 双通道字段对齐（门 3 比对基准）。
# 注意：本骨架为本地开发产物，路径为插件目录相对路径；提交市场时按
# 生态目录约定改为 plugins/<id>/ 前缀（generate_catalog 强制）。
PLUGIN_YAML_TEMPLATE = """\
id: {plugin_id}
name: {display_name}
version: {version}
publisher: your-author-name
category: source
summary: 契约 2 插件示例
description_file: listing.md
plugin_file: plugin.py
signature_file: plugin.py.sig
signature_algorithm: ed25519
# 创作者轨（可选但推荐）：签名 + 公钥身份
# creator_signature_file: creator.sig
# creator_identity_file: creator.identity
permissions: []
compatible_core: ">=0.9.1"
license: MIT
execution_mode: subprocess
dependencies: []
updated_at: "{today}"
# 发布前必读：门 1/门 3 一致性校验（omnicrawler plugins audit --local .）
"""
