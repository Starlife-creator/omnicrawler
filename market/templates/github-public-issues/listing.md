# GitHub 公开仓库 Issues

## 一句话简介
使用 GitHub 官方 REST API 获取公开仓库 Issue/PR 列表，支持分页与速率控制。

## 功能说明
- 采集源：GitHub REST API（api.github.com，公开仓库免 Token）
- 数据结构：JSONPath 抽取 number/title/state/url/创建时间/更新时间
- 分页：page 参数分页（可设截止页码）
- 速率控制：内置 2s 延迟 + 并发 1，遵守 GitHub 速率额度

## 适用场景
- 监控开源项目 Issue/PR 动态
- 导出某仓库全部历史 Issue 做分析
- 建立项目维护看板数据

## 占位符说明
| 占位符 | 必填 | 说明 |
|---|---|---|
| owner | ✓ | 仓库所有者 |
| repository | ✓ | 仓库名称 |
| end_page | ✗ | 截止页码（默认 10） |

## 兼容性
- `min_core_version: >=0.7.0`
- `license: GitHub API terms; repository content retains its original license`

## 使用方式
1. GUI 模板市场安装
2. 新建任务填写 owner/repository
3. 运行后 JSONL/CSV/XLSX 输出
