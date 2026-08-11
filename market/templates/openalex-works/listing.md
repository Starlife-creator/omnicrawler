# OpenAlex 学术目录

## 一句话简介
使用 OpenAlex 官方 API 检索论文目录；API Key 可选，匿名默认可用（100 次/分钟）。

## 功能说明
- 采集源：OpenAlex API（api.openalex.org，匿名可用）
- 数据结构：JSONPath 抽取 meta/results/group_by
- 凭证：`secret://openalex_api_key` 可选引用——**不填则匿名访问**（限 100 req/min），填 Key 可提额度
- 数据许可：OpenAlex 数据 CC0

## 适用场景
- 学术文献批量检索（与 Crossref 互补）
- 学科/机构维度分析
- 开放元数据研究

## 占位符说明
| 占位符 | 必填 | 说明 |
|---|---|---|
| query | ✓ | 检索词 |

## 兼容性
- `min_core_version: >=0.7.0`
- `license: OpenAlex data CC0; API subject to current service terms and usage budget`

## 使用方式
1. GUI 模板市场安装
2. 新建任务填写 query；如需要更高额度，在安全设置中配置 openalex_api_key
3. 运行后输出结果
