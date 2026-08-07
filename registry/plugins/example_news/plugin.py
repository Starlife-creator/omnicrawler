"""复制本文件即可开始编写真实网站适配器。"""

from omnicrawl.models import CrawlRequest
from omnicrawl.sources import GenericSource


class ExampleNewsSource(GenericSource):
    """示例：强制给入口附加站点所需请求头，并复用通用发现逻辑。"""

    def seed(self):
        requests = super().seed()
        for request in requests:
            request.headers["X-Requested-With"] = "XMLHttpRequest"
            request.meta["site"] = "example_news"
        return requests


def register(registry):
    registry.register_source("example_news", ExampleNewsSource)

