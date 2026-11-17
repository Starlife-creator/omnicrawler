import pytest

from tools.check_cli_docs import check_docs, cli_contracts, documented_commands


def test_documented_command_parser_only_reads_top_level_command():
    assert documented_commands("omnicrawler templates list\nomnicrawler run -c task.yaml") == {"templates", "run"}


def test_checked_project_docs_only_reference_real_commands():
    from pathlib import Path

    # 仓库根（DEFAULT_DOCS 声明的文档真实存在；缺失文件现在会被记为 issue）
    project_root = Path(__file__).resolve().parents[3]
    assert check_docs(project_root) == []


def test_cli_contracts_include_documented_nested_commands_and_options():
    contracts = cli_contracts()
    assert "render" in contracts["templates"]["subcommands"]
    assert "--set" in contracts["templates"]["options"]
    assert "--json" in contracts["stealth-fingerprint"]["options"]
    assert "--generate" in contracts["gen-templates"]["options"]
    assert "--url" in contracts["templates"]["options"]
    # 迁移后（2026-11-17）：`pdf` 的子命令与选项由**插件 pdf-workbench** 提供，
    # 宿主侧 `cli_contracts` 不再展开它们（插件未装时也展不开）。
    # 所以这里只在插件已装时断言——否则等于要求宿主依赖插件的内部结构。
    from omnicrawler.services.document_facade import document_capability

    pdf_contract = contracts["pdf"]
    assert isinstance(pdf_contract["subcommands"], set)  # 结构仍在（只是可能为空）
    if document_capability()["available"]:
        assert "ocr" in pdf_contract["subcommands"], (
            "插件已装，但 pdf 子命令未被展开——cli_contracts 展开逻辑失效"
        )
        assert "--page-number" in pdf_contract["options"]


def test_pdf_forwarded_documentation_uses_actual_stage_contract(tmp_path):
    """文档里写了不存在的 pdf 选项必须被检出。

    迁移后（2026-11-17）：`pdf` 的子命令/选项由插件提供，宿主侧
    `cli_contracts` 只在**插件已装时**才展开它们。未装时无可比对的契约，
    这条测试没有判据可依据——显式跳过，而不是让它红着
    （红着会让人误以为文档校验坏了）。
    """
    from omnicrawler.services.document_facade import document_capability

    if not document_capability()["available"]:
        pytest.skip("需要 pdf-workbench 插件：pdf 选项契约由插件提供")

    document = tmp_path / "commands.md"
    document.write_text('omnicrawler pdf --config "a directory/pdf.yaml" ocr --page-number 2\n')
    assert check_docs(tmp_path, paths=(document.name,)) == []
    document.write_text('omnicrawler pdf --config task.yaml fictional-stage --made-up 2\n')
    issues = check_docs(tmp_path, paths=(document.name,))
    assert any("unsupported subcommand fictional-stage" in issue for issue in issues)
    assert any("unsupported option --made-up" in issue for issue in issues)


def test_plugin_contract_docs_metadata_is_static_literal():
    """B01-015 根治（RC-1）：契约文档的 PLUGIN_METADATA 示例必须是静态字面量。

    加载器/静态检查器经 ast.literal_eval 解析它；实例形态会被拒。此测试保证
    文档示例与实现不再漂移（照抄文档必能通过 AST 预检）。
    """
    import ast
    import re
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[3]
    for rel in ("docs/PLUGIN_CONTRACT.md", "docs/ADDING_A_SITE.md"):
        text = (project_root / rel).read_text(encoding="utf-8")
        blocks = re.findall(r"```python\n(.*?)```", text, re.DOTALL)
        assert blocks, f"{rel} 应包含 Python 代码块"
        for block in blocks:
            if "PLUGIN_METADATA" not in block:
                continue
            tree = ast.parse(block)
            for node in tree.body:
                if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "PLUGIN_METADATA" for t in node.targets
                ):
                    # 必须是 ast.Dict 字面量（非 Call / Name）
                    assert isinstance(node.value, ast.Dict), (
                        f"{rel} 的 PLUGIN_METADATA 必须是 dict 字面量，"
                        f"不能是 dataclass 实例（ast.literal_eval 会拒）: {ast.dump(node.value)}"
                    )
                    # 字面量求值必须成功（所有值为静态字面量）
                    ast.literal_eval(node.value)

