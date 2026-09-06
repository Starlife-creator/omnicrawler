# docs/releases — 自动生成产物目录

> 本目录由工具链维护，**不要手写编辑或手工新建文件**。

## 目录职责

按版本存放发布报告，一份版本一份报告，命名固定为 `RELEASE_REPORT_<version>.md`。

| 文件 | 说明 |
|---|---|
| `RELEASE_REPORT_0.12.0.md` | 当前版本 0.12.0 的发布报告 |
| `RELEASE_REPORT_TEMPLATE.md` | 报告模板，新建版本时以此为底稿 |

## 谁来写这里

- **`tools/bump_version.py`**：版本 bump 时把 `RELEASE_REPORT_<old>.md` 重命名为
  `RELEASE_REPORT_<new>.md`，并校验新文件存在。
- **`tools/check_docs_consistency.py`**：把 `docs/releases/RELEASE_REPORT_<current>.md`
  列入必需文档清单，缺失即判定一致性检查失败。

因此**当前版本的报告文件不能删除**，否则发布前的一致性门禁会红。

## 历史报告在哪

旧编号体系（1.x / 2.x）的发布报告不是这里，而是
[`docs/archive/releases/`](../archive/README.md)。注意版本号经历过重置，
归档里那些"更大"的版本号实际时间更早，不要与当前 0.12.0 做大小比较。

## 维护约定

- 发布流程产出新报告后，确认文件名与 `pyproject.toml` 中的版本一致。
- 本门页由 `tools/check_docs_consistency.py` 校验存在性，请勿删除。
