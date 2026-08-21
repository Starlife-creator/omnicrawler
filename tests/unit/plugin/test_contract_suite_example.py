"""F1 验收：用 example_news（契约 2 样板）驱动公共契约测试套件。

作者继承 Contract2Suite 并覆盖 contract_plugin_dir 即可获得同一批测试
（本地绿 = CI 绿）。
"""

from pathlib import Path

import pytest

from omnicrawler.plugins.plugin_contract_suite import Contract2Suite


class TestExampleNewsContract(Contract2Suite):
    @pytest.fixture(scope="class")
    @staticmethod
    def contract_plugin_dir():
        return Path(__file__).resolve().parents[3] / "plugins_installed" / "example_news"
