import pytest

from omnicrawler.core.config import load_config
from omnicrawler.sources.frameworks import run_scrapy


def test_real_scrapy_offline_spider_delivers_verified_records(tmp_path):
    pytest.importorskip("scrapy")
    spider = tmp_path / "spider.py"
    spider.write_text("import scrapy\nclass Demo(scrapy.Spider):\n    name='offline'\n    async def start(self):\n        yield {'title': 'offline verified'}\n", encoding="utf-8")
    path = tmp_path / "task.yaml"
    # `spider_file` 用**绝对路径**：`config.resolve()` 会把相对路径按
    # `config.root`（= 项目根）解析，而 fixture 在 `tmp_path` 里。
    # 相对写法只有在 cwd 恰好是 tmp_path 时才碰巧成立 —— 实测报
    # `FileNotFoundError: Scrapy spider不存在: <仓库根>/spider.py`。
    path.write_text(
        f"project: {{name: bridge, workspace: {tmp_path.as_posix()}/ws}}\n"
        f"source: {{kind: scrapy, spider_file: {spider.as_posix()}, "
        "execution_contract: trusted_external, timeout_seconds: 20}\n",
        encoding="utf-8",
    )
    result = run_scrapy(load_config(path))
    assert result["status"] == "succeeded" and result["records"] == 1
    assert result["network_contract"] == "trusted_external_unmanaged"
