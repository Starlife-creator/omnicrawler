# OmniCrawler 1.4.0 发行报告

发行日期：2026-07-22

## 交付结论

1.4.0 已完成认证独立本地 Worker、GUI 默认 Worker 适配与重连、工作区格式与三种包、体检、升级快照/
回滚、三种数据模式、移动盘提示、签名组件管理、断点续传、运行时清单、安全升级器及 Standard/Full
分层构建链。FutureRemoteBackend 只保留协议，未在 UI 暴露。

## 本地验证

| 门禁 | 结果 |
|---|---|
| 全量 Pytest | 185 passed，2 skipped |
| 真实浏览器与离屏 GUI | 4 passed |
| 全源码覆盖率 | 65.72%，门槛60% |
| 安全与状态 | 89.47%，门槛85% |
| 管线/HTTP/来源 | 75.05%，门槛75% |
| 浏览器/API | 81.57%，门槛70% |
| PDF/OCR | 66.97%，门槛65% |
| 桌面核心（新 WorkerTaskRunner） | 69.18%，门槛60% |
| Mypy | 95个源文件零错误 |
| Ruff | 通过 |
| Python源码编译 | 158个文件通过 |
| Worker真实命名管道认证/重连 | 通过 |
| Windows脚本与Standard/Full spec语法 | 通过 |

## 制品

- Wheel：421,090字节；SHA-256 `6c87e0d85d9552a9d7328eecc02ac0a84772c305e84b7ac1d16089816905f2cf`
- SBOM：34,965字节；SHA-256 `c6eab7581c3474a710749e67611fc1bed9498ed91baba748640698823b01eb9d`

## 外部验收保持 pending

- Standard/Full ZIP 在干净 Windows 机器的完整构建与复制迁移验收；
- Authenticode：构建链已接入，但当前没有发行证书，未执行 signtool；
- Docker与托管跨系统/Python矩阵。

未执行项没有被标记为通过。
