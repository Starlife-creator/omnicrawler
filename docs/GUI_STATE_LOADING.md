# GUI 状态加载线程边界（C-#13）

> 适用版本：1.0.1 · 配置协议：v5 · 维护状态：现行（历史状态见归档；功能验证以验收账本为准）

本文记录 GUI 视图在 UI 线程直连 `StateStore` 的存量点位、已测量负载、异步迁移目标与线程纪律。配套静态门禁：`tools/check_gui_state_threading.py`（禁止新增直连，存量点位只降不升）。

## 1. 线程纪律

- **UI 线程**：只允许操作控件（增删项目、填表格、刷新下拉），不得执行可能阻塞事件循环的数据库查询。
- **后台线程**：经 `gui/core/state_query_service.py` 适配层读取状态库；**不得实例化、不得触碰任何 Qt 控件**；查询结果经 `BackgroundWorker.finished_*` 信号回投 UI 线程后，再由槽函数写入控件。
- 状态库连接按线程独立创建（`sqlite3.connect(..., check_same_thread=False)` 仅用于既有 `SqliteQueryWorker` 骨架；跨线程共享连接不在本项目支持范围内）。

## 2. 存量直连点位基线

| 点位 | 查询内容 | 负载记录 | 处置结论 |
| --- | --- | --- | --- |
| `gui/views/developer_inspector.py:334` | `list_runs(50)` 刷新运行下拉 | 200 次运行 / 100k 条事件实测 0.3 ms（见 `gui/core/workers.py` `SqliteQueryWorker` docstring） | 当前不阻塞，保留同步；数量计入门禁基线 |
| `gui/views/developer_inspector.py:365` | `list_runs(500)` + `run_events` + `run_stages` | 同上场景 0.5 / 0.7 / 0.0 ms | 当前不阻塞，保留同步；数量计入门禁基线 |
| `gui/views/project_dialogs.py:55` | 最近 30 条运行 `rows(...)` 对比选择器 | ⬜ 待补耗时测量（轻量单行 SQL，当前未做事件量级实测） | 保留同步；下次负载复测时补记录 |
| `gui/views/reprocess_review.py:37` | `ReprocessReview(state).load(record_id)` 快照 | ⬜ 待补耗时测量（单记录读取） | 保留同步；弹窗构建期一次性读取 |
| `gui/views/reprocess_review.py:101` | `ReprocessReview(state).resolve(...)` 写回复核结果 | 写路径，受用户操作触发，非轮询 | 保留同步；错误已就地提示 |

## 3. 适配层（骨架就位，尚未接线）

`gui/core/state_query_service.py` 把不接触控件的查询收拢为纯函数：

- `load_recent_runs(db_path, limit=50)`：最近运行记录；库不存在或异常返回空列表；
- `load_timeline(db_path, run_id)`：`(run, events, stages)` 三元组；
- `load_run_selector_rows(db_path, limit=30)`：对比选择器行。

迁移前，既有视图继续直连 `StateStore`；适配层先作为唯一合法封装点接受门禁约束。

## 4. 迁移触发条件与步骤

出现以下任一情形，先在目标数据量下重测，再启动后台迁移：

1. 状态库事件量突破当前测量量级（100k 条事件）一个数量级；
2. `list_runs` / `run_events` 单次实测超过 50 ms；
3. 新增视图需要在弹窗打开时读取全量历史记录。

迁移步骤：

1. 把查询语义迁入 `state_query_service` 对应函数（同步调用点与 worker 复用同一实现）；
2. 以 `BackgroundWorker`（或既有 `SqliteQueryWorker`）在后台线程调用该函数，禁止在 `work()` 内触碰控件；
3. 结果信号回投 UI 线程，槽函数内完成控件写入；失败信号回投错误提示；
4. 删除视图内直连点位，同步更新 `tools/check_gui_state_threading.py` 基线计数。

## 5. 待办与环境依赖

- `project_dialogs.py:55`、`reprocess_review.py:37,101` 三处补负载测量记录（需可构造大容量状态库的测试环境）；
- 后台线程迁移后的 widget 信号契约测试待 Qt offscreen CI 环境补齐；
- 本文件与门禁只约束「不新增、不超基线」，不替代上述复测与迁移。
