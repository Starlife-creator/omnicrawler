# example_news — 示例新闻站点适配器

> 本文件是插件提交的**强制功能说明**（listing）。每一份进入生态的插件都必须附带这样一份说明，供审核者与使用者快速判断"这个插件做什么、什么时候用、要什么权限"。

## 一句话简介
为某个示例新闻站点强制注入 `X-Requested-With: XMLHttpRequest` 请求头，并在每个入口请求的 `meta` 中打上 `site=example_news` 标记，其余完全复用 OmniCrawler 的通用 URL 发现逻辑。

## 功能说明
- **请求头注入**：重写了 `seed()`，对父类的入口请求逐个追加 AJAX 请求头，规避站点对"非 XHR 请求"的反爬拦截。
- **站点打标**：在 `request.meta["site"]` 写入 `"example_news"`，方便后续在导出/清洗阶段按站点分流。
- **复用通用发现**：不重写抓取与发现逻辑，仅做最小增强，降低维护成本与漂移风险。

## 适用场景
- 目标站点要求 `X-Requested-With` 头才返回正文。
- 需要把某一类入口统一打上站点标签以便下游区分。
- 作为"如何写一个 Source 插件"的参考模板。

## 不属于它的范围
- 不处理登录态、不解析页面结构、不做字段抽取——这些交给 auth / parser / extractor 插件或内置能力。
- 不修改任何全局配置或网络栈。

## 权限声明
无特殊权限（`permissions: []`）。它只通过平台提供的 `GenericSource` 接口操作自己的请求对象，不触达文件系统、网络出口或凭证。

## 兼容性
- `compatible_core: ">=2.7.0"`
- 仅依赖 `omnicrawl.models.CrawlRequest` 与 `omnicrawl.sources.GenericSource`（稳定 API）。

## 使用方式
1. 安装后，在任务的 `source.kind` 指向此插件注册的源名 `example_news`（或经由插件市场一键启用）。
2. 平台加载时会在 `exec_module` 之前用信任根公钥验签；验签不通过则 fail-closed 拒载。

## 作者与版本
- 作者 / 发布者：`Starlife-creator`
- 版本：`1.0.0`
- 许可：`MIT`

## 安全备注
- 本插件是模板示例，仅用于演示 Source 扩展点的签名与发布流程。
- 真实插件提交前必须通过 `tests/unit/plugin/` 契约测试，并由持有冷私钥的发布者签名。
