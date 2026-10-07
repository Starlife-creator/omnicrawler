import pytest

from omnicrawler.core.config import load_config
from omnicrawler.sources.frameworks import run_scrapy


def test_real_scrapy_offline_spider_delivers_verified_records(tmp_path):
    pytest.importorskip("scrapy")
    spider = tmp_path / "spider.py"
    spider.write_text("import scrapy\nclass Demo(scrapy.Spider):\n    name='offline'\n    async def start(self):\n        yield {'title': 'offline verified'}\n", encoding="utf-8")
    path = tmp_path / "task.yaml"
    path.write_text("project: {name: bridge, workspace: work}\nsource: {kind: scrapy, spider_file: spider.py, execution_contract: trusted_external, timeout_seconds: 20}\n", encoding="utf-8")
    result = run_scrapy(load_config(path))
    assert result["status"] == "succeeded" and result["records"] == 1
    assert result["network_contract"] == "trusted_external_unmanaged"
