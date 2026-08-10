from tools.check_cli_docs import check_docs, cli_contracts, documented_commands


def test_documented_command_parser_only_reads_top_level_command():
    assert documented_commands("omnicrawl templates list\nomnicrawl run -c task.yaml") == {"templates", "run"}


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
