# 新增站点：优先模板，必要时插件

## 先用现有能力

1. `omnicrawl templates inspect <url>` 探测内容类型、CMS、JSON-LD、动态壳和 API 线索。
2. 在 GUI 模板库搜索行业、协议或 CMS。
3. 用小样本配置 CSS/XPath/JSON path 字段并核对证据。
4. 只有分页游标、签名、数据格式或导出确实特殊时才写插件。

验证码、付费墙和未授权访问不是插件适配目标。

## 站点模板要求

```yaml
template:
  id: sites/example-notices
  name: Example 公告
  category: sites/government
  description: 使用该站公开 API 获取公告
  version: 1.0.0
  tags: [公告, API]
  capabilities: [rest, json]
  domains: [example.org]
  placeholders:
    keyword: {label: 关键词, required: true}
  source_urls: [https://example.org/api-docs]
  license: API terms; content retains original copyright
  verified_at: '2026-07-18'
```

模板必须校验：`omnicrawl templates validate --include-legacy`。不要在模板保存 Cookie、Token、个人数据或抓取快照。

## 最小 source 插件

```python
from omnicrawl.models import CrawlRequest
from omnicrawl.sources import GenericSource

PLUGIN_METADATA = {
    "name": "example-site",
    "version": "1.0.0",
    "api_version": 1,
    "plugin_types": ("source",),
    "capabilities": ("cursor-pagination",),
    "domains": ("example.org",),
    "permissions": ("network",),
    "license": "MIT",
    "fallback": "rest",
}

class ExampleSource(GenericSource):
    def seed(self):
        return [CrawlRequest(
            "https://example.org/api/notices",
            headers={"Accept": "application/json"},
            meta={"root_url": "https://example.org/"},
        )]

    def discover(self, result):
        # 解析服务端游标，返回下一页 CrawlRequest；必须有终止条件。
        return super().discover(result)

def register(registry):
    registry.register_source("example_site", ExampleSource)
```

配置：

```yaml
source: {kind: example_site, seeds: [https://example.org/]}
plugins:
  paths: [plugins/example_site.py]
  approved_permissions: [network]
```

完整插件类型和签名见 `PLUGIN_CONTRACT.md`。

## 验收清单

- 稳定 URL、方法和 body 产生稳定指纹。
- 相对 URL 规范化，重定向后仍执行范围检查。
- 分页有停止条件和最大页数。
- 认证只用 `secret://` 或 auth provider。
- 429/5xx 按 Retry-After 和退避处理，不忙重试。
- 每个字段有来源、路径、原值、清洗值和置信度。
- 插件失败在单 URL 隔离；fallback 行为明确。
- 用本地 HTTP fixture 测分页、认证、异常、恢复和导出，默认测试不依赖公网。
