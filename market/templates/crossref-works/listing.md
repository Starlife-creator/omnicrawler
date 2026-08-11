# Crossref 学术文献元数据

## 一句话简介
使用 Crossref 官方 REST API 按关键词检索论文元数据，保留 DOI、作者、出版商等结构化信息。

## 功能说明
- 采集源：Crossref REST API（api.crossref.org，开放公共 API）
- 数据结构：JSONPath 抽取 DOI/标题/类型/发表日期/URL/许可证
- 分页：cursor 分页（next-cursor），支持深度检索
- 礼貌请求：自动携带 mailto 联系邮箱参数，符合 Crossref 服务条款

## 适用场景
- 按关键词批量检索学术文献元数据
- 建立论文 DOI/引用信息数据集
- 期刊/出版物的结构化监控

## 占位符说明
| 占位符 | 必填 | 说明 |
|---|---|---|
| query | ✓ | 检索词 |
| contact_email | ✓ | 维护者联系邮箱（API 礼貌头，建议真实邮箱） |

## 兼容性
- `min_core_version: >=0.7.0`
- `license: Crossref metadata terms; abstracts may retain publisher copyright`

## 使用方式
1. 在 GUI 模板市场安装（信任根验签通过）
2. 新建任务选择本模板，填写 query 与 contact_email
3. 运行后 JSONL/CSV/XLSX 三种输出格式
