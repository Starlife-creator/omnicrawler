# 性能基准：可复现与公平对比

> 适用版本：0.12.0 · 对应阶段：第四阶段（完整体验与 UI）· 维护状态：现行

## 这份文档解决什么

`优化方案.md` §一 要求「用公开、可复现、可比较的任务结果证明各项优势」，§1.2 对「采集能力」
进一步升级为「**具备可复现的任务基准与公平对比能力**」。

这不是形容词，而是三条可被检验的约束：

| 约束 | 含义 | 落地方式 |
|---|---|---|
| 档位真正生效 | 不同档位必须跑**不同的负载** | `apply_profile()` 把档位写入派生配置 |
| 结果自带复现信息 | 他人能重建这次运行 | 源/生效配置指纹 + 档位参数 + 环境 |
| 只有可用运行才成为基线 | 退化检测不能被零吞吐基线废掉 | `BenchmarkResult.usable` |

## 怎么跑

```bash
omnicrawler benchmark -c configs/full_pipeline.yaml --profile all
```

| 参数 | 说明 |
|---|---|
| `--profile` | `low` / `standard` / `high` / `all`（默认 `all`） |
| `--output` | 基准历史 JSON 路径（默认 `bench_history.json`） |
| `--history` | 从已有历史读取基线（默认同 `--output`） |
| `--regression-threshold` | 吞吐量退化告警阈值（默认 `0.1` 即 10%） |

## 三个档位

| 档位 | 并发 | 请求间隔 | 超时 | 页数上限 |
|---|---|---|---|---|
| `low` | 1 | 2.0s | 30s | 10 |
| `standard` | 3 | 1.0s | 25s | 50 |
| `high` | 8 | 0.3s | 20s | 200 |

**档位定义负载，因此它覆盖被跑分配置自身的** `crawl.concurrency`、`crawl.max_pages`、
`http.delay_seconds`、`http.timeout_seconds`。

为什么是「覆盖」而不是「取较小值」：若让配置获胜，同一配置在不同档位下会跑出不可比的负载，
「档位」就只是一个标签。反过来，只要档位固定、源配置相同，任何人都能重建同一次运行。

派生配置写入 `<配置所在目录>/.benchmark/profile-<档位>.yaml`。**刻意使用固定路径**——
复现一次基准时，实际执行的是哪份配置应当可以被直接打开查看，而不是藏在随机临时目录里。

### 复现一次运行

持有结果中的 `config_sha256`、`profile_settings` 与 `environment`，即可：
用同版本的包、同一份源配置、同名档位重跑。`apply_profile()` 是确定性的，
所以派生配置的 `effective_config_sha256` 应当一致。

## 结果里有什么

| 字段 | 含义 |
|---|---|
| `profile` | 档位名 |
| `pages` | 本次落库的响应数（吞吐量的分子） |
| `duration_seconds` | 墙钟耗时 |
| `pages_per_second` / `seconds_per_thousand_pages` | 派生吞吐指标 |
| `peak_memory_bytes` | 峰值内存（见「已知限制」） |
| `bytes_transferred` | **落库响应字节数**（非网络传输量） |
| `errors` | 该次运行的错误计数（口径见「已知限制」） |
| `ok` | 流水线是否以 `succeeded` 结束 |
| `status` | 流水线终态（`succeeded` / `failed` / `cancelled`） |
| `config_sha256` | 源配置指纹 |
| `effective_config_sha256` | 施加档位后实际执行的那份配置的指纹 |
| `profile_settings` | 本次所用档位参数（用于确定性重建） |
| `environment` | 包版本 / Python / 实现 / 平台 / 解释器路径 |

## 判据：`ok` 与 `usable` 是两件事

| 判据 | 含义 |
|---|---|
| `ok` | 流水线是否以成功状态结束——**只陈述运行结局** |
| `usable` | `ok ∧ pages > 0`——**能否作为一次基准测量** |

只有 `usable` 为真的运行才会被 `BenchmarkHistory.baseline()` 选为基线。

原因：吞吐量是「页 / 秒」。一页都没取到时这个比值无从谈起；若这种运行入库并成为基线，
之后任何真实运行都会显得「比基线更好」，**该档位的退化检测就此静默失效**。

## 如何比较两次结果

```bash
# 首次运行建立基线
omnicrawler benchmark -c config.yaml --profile standard

# 改动后再次运行，自动与该档位基线比较
omnicrawler benchmark -c config.yaml --profile standard
```

比较使用 `compare_benchmark()`：以档位最早的一次**可用**运行作为基线，吞吐量下降超过阈值
（默认 10%）即标记退化。跨档位比较无意义——档位不同，负载就不同。

## 已知限制

如实列出，避免把「看起来有值」当作「测到了」：

1. **峰值内存是采样值**：通过流水线事件回调节流采样（间隔 ≥ 50ms），不是逐次分配的精确峰值；
   若运行期间没有事件，退化为「运行前后取较大者」。用于发现数量级变化，不适合精细对比。
2. **流水线在「全部抓取失败」时仍报 `succeeded`**：实测种子全部连接失败时
   `status="succeeded"`、`pages=0`、`errors=0`。本模块用 `usable` 兜住了它对基线的污染，
   但**流水线层面的「异常伪装成功」本身尚未修正**——属 §1.2 可靠性升级项
   「故障可注入 / 可定位 / 可恢复」的范围。
3. **`errors` 不是失败请求的完整计数**：抓取失败未必写入 `errors` 表
   （实测被出口策略拦截时不计数）。
4. **`bytes_transferred` 的口径是落库字节**：从状态库聚合 `responses.size_bytes`，
   不含被丢弃的响应、压缩前体积与响应头。
5. **本模块测的是流水线吞吐，不是抽取质量**：数据完整性 / 准确性属于「可复现的任务基准」
   的下一层（见 §1.2 采集能力的完整表述），尚未建设。

## 相关

- 微基准（模板发现、HTML 抽取耗时）：`tools/benchmark_core.py`
- 转换专项基准：`tools/benchmark_convertx.py`
- 端到端公平性回归：`tests/integration/test_benchmark_fairness.py`
- 单元测试：`tests/unit/utils/test_benchmarking.py`
