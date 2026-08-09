import scrapy


class ExampleSpider(scrapy.Spider):
    name = "omnicrawl_example"
    custom_settings = {"ROBOTSTXT_OBEY": True, "DOWNLOAD_DELAY": 1.0}

    def __init__(self, start_url="https://example.com/", **kwargs):
        super().__init__(**kwargs)
        self.start_urls = [start_url]

    def parse(self, response):
        yield {
            "url": response.url,
            "title": response.css("title::text").get(),
            "heading": response.css("h1::text").get(),
        }

