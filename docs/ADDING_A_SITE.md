# 新增站点：优先模板，必要时插件

## 先用现有能力

1. `omnicrawler templates inspect <url>` 探测内容类型、CMS、JSON-LD、动态壳和 API 线索。
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

模板必须校验：`omnicrawler templates validate --include-legacy`。不要在模板保存 Cookie、Token、个人数据或抓取快照。

## 最小 source 插件

```python
PLUGIN_METADATA = {
    "name": "example-site",
    "version": "1.0.0",
    "api_version": 1,
    "description": "从 Example 公开 API 生成抓取请求",
    "plugin_types": ("source",),
    "permissions": ("network:scoped",),
    "domains": ("example.org",),
    "input_files": (),
    "dependencies": [],
    "license": "MIT",
    "execution_mode": "subprocess",
    "min_core_version": "0.11.2",
    "source_url": "https://example.org/api-docs",
}

def handle(operation, payload):
    if operation == "source.seed":
        return {
            "requests": [
                {
                    "url": "https://example.org/api/notices",
                    "method": "GET",
                    "headers": {"Accept": "application/json"},
                }
            ]
        }
    return {"error": "unsupported_operation", "operation": operation}
```

需要网络响应或宿主数据时，通过 `omnicrawler_sdk.call(...)` 请求已声明的能力；不要导入
`omnicrawler` 内部模块。分页游标等状态必须通过纯数据 payload 传递，并设置终止条件和最大页数。

生成同类插件的推荐起点：

```powershell
python -m omnicrawler.cli plugins scaffold-contract2 `
  --plugin-id example_site `
  --display-name "Example Site" `
  --output-dir plugins
```

完整插件契约、权限和签名流程见 `PLUGIN_CONTRACT.md` 与 `AUTHOR_GUIDE.md`。

## 验收清单

- 稳定 URL、方法和 body 产生稳定指纹。
- 相对 URL 规范化，重定向后仍执行范围检查。
- 分页有停止条件和最大页数。
- 认证只用 `secret://` 或 auth provider。
- 429/5xx 按 Retry-After 和退避处理，不忙重试。
- 每个字段有来源、路径、原值、清洗值和置信度。
- 插件失败在单 URL 隔离；fallback 行为明确。
- 用本地 HTTP fixture 测分页、认证、异常、恢复和导出，默认测试不依赖公网。
