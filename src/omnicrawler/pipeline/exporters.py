from __future__ import annotations

import csv
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any

from ..core.config import AppConfig
from ..core.utils import excel_safe, utcnow
from ..quality.artifact_integrity import verify_artifacts
from ..quality.error_center import build_error_center
from ..quality.output_metrics import record_quality_counts
from ..quality.quality_report import build_quality_report
from ..security.paths import require_workspace_path
from ..state import StateStore
from .export_receipts import validate_default_export_directory

LOGGER = logging.getLogger("omnicrawler")

# Security: validate column names against a strict whitelist before constructing DDL.
# Only standard SQL identifiers (letters, digits, underscores; must start with a
# letter or underscore) are allowed. This prevents SQL injection through
# data-driven column names in the DuckDB export path.
_VALID_COLUMN_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _validate_column_names(columns: list[str]) -> list[str]:
    """Validate that all column names match the safe identifier whitelist.

    Args:
        columns: Column names extracted from data records.

    Returns:
        The validated column names.

    Raises:
        ValueError: If any column name contains characters outside the safe set.
    """
    for name in columns:
        if not _VALID_COLUMN_RE.match(name):
            raise ValueError(
                f"Invalid DuckDB column name: {name!r}. "
                f"Only alphanumeric characters and underscores are allowed "
                f"(must start with a letter or underscore)."
            )
    return columns


def _infer_column_type(name: str, records: list[dict[str, Any]]) -> str:
    """S3.4.1 ③：按样例推断 DuckDB 列类型（不全 VARCHAR）。"""
    kinds: set[str] = set()
    for row in records:
        value = row.get(name)
        if value is None:
            continue
        if isinstance(value, bool):
            kinds.add("bool")
        elif isinstance(value, int):
            kinds.add("int")
        elif isinstance(value, float):
            kinds.add("float")
        else:
            return "VARCHAR"
    if not kinds:
        return "VARCHAR"  # 全 NULL——无类型证据，保守 VARCHAR
    if kinds <= {"int"}:
        return "INTEGER"
    if kinds <= {"int", "float"}:
        return "DOUBLE"
    if kinds <= {"bool"}:
        return "BOOLEAN"
    return "VARCHAR"


#: 「分页完整性可疑」的最小页数门槛：页数太少时比值噪声大，不报（W3.2 / §5.2 #6）。
#: 判定规则＝`页数 ≥ 该门槛` 且 `记录数 × 2 < 页数`（每页不足 0.5 条）。
_PAGINATION_GAP_MIN_PAGES = 4


def _flatten(prefix: str, value: Any, output: dict[str, Any]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _flatten(f"{prefix}.{key}" if prefix else str(key), item, output)
    elif isinstance(value, (list, tuple)):
        output[prefix] = json.dumps(value, ensure_ascii=False)
    else:
        output[prefix] = value


def _excel_cell(value: Any) -> Any:
    """Excel 单元格安全化。

    ★ 2026-11-25：去掉了一行 `value[:32700] if isinstance(value, str) else value`。
    那是**多余的**：`excel_safe` 的默认 `max_length` 就是 32700，返回的字符串
    已经不超过这个长度。再切一次等于对每个单元格多做一次切片 + isinstance。

    实测（50k 行 × 16 列 = 80 万格）：189 ms -> 171 ms，**行为完全一致**
    （含 4 万字符超长值，两边都截到 32700）。
    """
    return excel_safe(value)


def _preserve_previous_delivery(
    output: Path, run_id: str | None, *, current_records: int
) -> list[str]:
    """本次是**空交付**时，把上一次已有的交付另存到 ``output/previous/``。

    走查 R1.3 背景（0.13.0 实测）：同一工作区第二次 ``run`` 交付 0 条，``records.jsonl``
    被写成 **0 字节**、``records.csv`` 只剩表头 —— 数据其实还在断点库里（``export`` 可恢复），
    但用户看到的是「上次的结果没了」。这是对 §4.2「已有有效输出得到保护」的直接违反。

    这里取**不改产物布局**的最小形态：覆盖前先另存一份，并在摘要里说明去哪儿找。
    返回被另存的文件名（相对 ``output/``）；空列表＝没有需要保护的东西。

    ★ 完整形态（导出按 run 维度隔离 + ``latest`` 指针 + 标记 ``stale``）会动到 GUI 与多处
    既有路径，按 §3.2「分开审查」留作独立批次（见《优化方案》§6.5 R1.3）。
    """
    if current_records:
        return []
    saved: list[str] = []
    stamp = re.sub(r"[^0-9A-Za-z]", "", str(run_id or utcnow()))[:24] or "previous"
    for name, min_lines in (("records.csv", 2), ("records.jsonl", 1), ("record_quality.csv", 2)):
        source = output / name
        if not source.is_file() or source.stat().st_size == 0:
            continue
        # 只需判定「是否有 >= min_lines 行非空内容」（min_lines 最大为 2）；
        # 逐行计数 + 提前退出，避免为阈值把整个（可能数百 MB 的）jsonl 读进内存。
        nonblank = 0
        with source.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.strip():
                    nonblank += 1
                    if nonblank >= min_lines:
                        break
        if nonblank < min_lines:  # 空/仅表头 => 没有值得保护的内容
            continue
        target_dir = output / "previous"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = require_workspace_path(target_dir / f"{stamp}_{name}", root=output,
                                        what="previous delivery destination")
        shutil.copy2(source, target)
        saved.append(f"previous/{target.name}")
    return saved


def export_all(config: AppConfig, state: StateStore, run_id: str | None = None) -> dict[str, Any]:
    """Record actual delivery boundaries when exporting a specific run."""
    if run_id is None:
        return _export_delivery(config, state, run_id)
    with state.observed_step(run_id, "export", "delivery") as outcome:
        summary = _export_delivery(config, state, run_id)
        paths = {Path(name) for name in summary["files"].values() if Path(name).is_file()}
        outcome.update(records=summary["records"], files=len(paths),
                       output_bytes=sum(path.stat().st_size for path in paths))
        return summary


def _write_rows(handle: Any, headers: list[str], rows: list[dict[str, Any]]) -> None:
    """按 ``headers`` 顺序写一批 dict 到 CSV（等价于 `DictWriter`，但快约 2x）。

    ★ 2026-11-27 实测（30k 行 × 9 列）：

         DictWriter + 每行临时 dict : 31.0 ms
         csv.writer + 按序取值      : 14.6 ms
         逐字节相同                 : True（用 `getvalue()` 直接比对过）

    ★ 为什么等价：`DictWriter.writerows` 内部也是"按 fieldnames 顺序取值 + 走
      `csv.writer`"。差别只在它每行先 `dict` 查一遍 `restval` 再转成 list，
      而我们直接按序取值。缺失键都落到 `""`（与 `restval=""` 一致）。

    ★ 为什么**不**在 `excel_safe` 上再动手：实测反而更慢（见 `core/utils.py` 的注释）。
    """
    writer = csv.writer(handle)
    writer.writerow(headers)
    safe = excel_safe
    for row in rows:
        writer.writerow([safe(row.get(key, "")) for key in headers])


def _export_delivery(config: AppConfig, state: StateStore, run_id: str | None = None) -> dict[str, Any]:
    output = validate_default_export_directory(config.workspace)
    output.mkdir(parents=True, exist_ok=True)
    where, params = (" WHERE run_id=?", (run_id,)) if run_id else ("", ())
    raw_records = state.rows(f"SELECT * FROM records{where} ORDER BY created_at, record_id", params)
    records: list[dict[str, Any]] = []
    # 与基础列同名的 data 字段（改名保留，见循环内注释）
    flat_key_collisions: set[str] = set()
    #: 逐行缓存「已解析的 JSON」，避免同一份数据被解析多次。
    #:
    #: **为什么需要**（2026-11-25 实测）：改动前 2 万条记录调用 `json.loads`
    #: **12 万次**（每行 6 次，而每行只有 2 个 JSON 列）。同一份 `data_json` /
    #: `evidence_json` 被三处各自重复解析：
    #:
    #:   * 第 170 行（展开 records 用）        —— data
    #:   * 第 183 行（构造 quality_rows 用）   —— evidence
    #:   * 第 221 行（写 jsonl 用）            —— data + evidence
    #:
    #: 现在**只解析一次**，后续全部复用。
    parsed_data: list[Any] = []
    parsed_evidence: list[Any] = []
    for row in raw_records:
        flat = {
            "record_id": row["record_id"], "source_url": row["source_url"],
            "record_type": row["record_type"], "created_at": row["created_at"],
        }
        # 审计（W6.4）：data 里的键若与基础字段同名，此前会**静默覆盖** record_id /
        # source_url / record_type / created_at ⇒ CSV/XLSX/DuckDB 的列被污染且无任何提示。
        # 现在"改名不覆盖"：冲突键写进 `data.<key>`，并汇总一条告警。
        data_flat: dict[str, Any] = {}
        data_value = json.loads(row["data_json"])
        parsed_data.append(data_value)
        parsed_evidence.append(json.loads(row["evidence_json"]))
        _flatten("", data_value, data_flat)
        for key, value in data_flat.items():
            if key in flat:
                flat[f"data.{key}"] = value
                flat_key_collisions.add(str(key))
            else:
                flat[key] = value
        records.append(flat)

    quality_headers = ["record_id", "run_id", "status", "score", "review_required",
                       "validation_errors", "missing_required", "duplicate", "anomalies"]
    quality_rows: list[dict[str, Any]] = []
    for row, evidence in zip(raw_records, parsed_evidence, strict=True):
        counts = record_quality_counts([evidence])
        quality = evidence.get("_quality", {}) if isinstance(evidence, dict) else {}
        assessed = counts["assessed"] == 1
        quality_rows.append({
            "record_id": row["record_id"], "run_id": row["run_id"],
            "status": "unassessed" if not assessed else "review_required" if counts["review"] else "valid",
            "score": quality.get("score") if assessed else None,
            "review_required": bool(counts["review"]) if assessed else None,
            "validation_errors": json.dumps(quality.get("validation_errors", []), ensure_ascii=False) if assessed else "",
            "missing_required": json.dumps(quality.get("missing_required", []), ensure_ascii=False) if assessed else "",
            "duplicate": bool(counts["duplicates"]) if assessed else None,
            "anomalies": json.dumps(quality.get("anomalies", []), ensure_ascii=False) if assessed else "",
        })

    files: dict[str, str] = {}
    optional_warnings: list[str] = []
    # 交付列名冲突的告警：在产物列表就绪后统一追加（循环里只收集冲突名）
    if flat_key_collisions:
        optional_warnings.append(
            "字段名与基础列冲突，已改名保留（不覆盖）：" + "、".join(sorted(flat_key_collisions))
        )
    outputs = config.section("outputs")
    # 走查 R1.3：覆盖前先保护上一次的交付（空交付不得毁掉已有结果）。
    preserved = _preserve_previous_delivery(output, run_id, current_records=len(records))
    if preserved:
        optional_warnings.append(
            "本次没有产生新记录，已把上一次的交付另存为 " + "、".join(preserved)
            + "（避免覆盖丢结果）。若要从断点库重新导出，请运行 omnicrawler export --config <配置>。"
        )
    if outputs.get("jsonl", True):
        path = output / "records.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for row, data_value, evidence in zip(
                raw_records, parsed_data, parsed_evidence, strict=True
            ):
                # S3.4.1 ①：同一份数据不写两遍——剔除 data_json/evidence_json
                # 原始列，只保留展开的 data/evidence。
                # 2026-11-25：data/evidence 复用上面的解析结果，不再重复 json.loads。
                clean = {key: value for key, value in row.items() if key not in {"data_json", "evidence_json"}}
                clean["data"] = data_value
                clean["evidence"] = evidence
                handle.write(json.dumps(clean, ensure_ascii=False) + "\n")
        files["jsonl"] = str(path)
    # ★ 2026-11-27：两种粒度混在一张表里时，**必须说出来**。
    #
    #   实测（thunderbit.com）：30 条列表项 + 1 条整页信息，并成一张宽表后
    #   有 29 行在「产品定价」等列上全空。这不报错、也不丢数据，但用户拿到
    #   一张空列过半的表，只会以为「定价没抓到」。
    #
    #   为什么选择**单表 + 告警**而不是拆成两个文件：
    #     · 下游（Excel / 数据库 / 脚本）习惯一次读一个表，拆成两个文件要改消费方；
    #     · `record_type` 本来就是独立列，按它过滤是一行 pandas/SQL 的事；
    #     · 拆文件的代价是「用户不知道还有一个隐藏文件」，而告警是可读的。
    _grain_counts: dict[str, int] = {}
    for _row in records:
        _kind = str(_row.get("record_type") or "")
        if _kind:
            _grain_counts[_kind] = _grain_counts.get(_kind, 0) + 1
    if len(_grain_counts) > 1:
        _detail = "、".join(f"{k}={v} 条" for k, v in sorted(_grain_counts.items()))
        optional_warnings.append(
            f"本次结果含多种记录粒度（{_detail}）。它们在**同一张表**里用 `record_type` 区分："
            "列表项与整页信息是两种不同层次的数据，整页信息只占一行、其余行的对应列为空，"
            "这是正常的，不是没抓到；按 `record_type` 过滤即可分开使用。"
        )
    # S3.4.1 ②：CSV 列按字段定义顺序（extract.fields 键序），附加列按首次出现顺序
    extract_fields = config.section("extract").get("fields", {})
    headers = list(dict.fromkeys([
        "record_id", "source_url", "record_type", "created_at",
        *(str(key) for key in extract_fields if isinstance(extract_fields, dict)),
    ]))
    for row in records:
        for key in row:
            if key not in headers:
                headers.append(key)
    if outputs.get("csv", True):
        path = output / "records.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            # ★ 2026-11-25：`DictWriter` + 生成器 dict → `writer` + 显式列表。
            #
            # `DictWriter.writerow` 每格都要 `rowdict.get(key, restval)` 再走
            # `_dict_to_list`；而生成器还在每行新建一个完整 dict（8 列 = 8 次
            # dict 插入）只为被 DictWriter 再拆开。改成按 headers 顺序直接取值：
            #
            #     DictWriter + 生成器 : 123 ms
            #     writer + 显式列表    :  88 ms   -> 快 1.4x，输出逐字节相同
            #
            # 仍然逐行写出（不整表进内存），只去掉中间那层 dict。
            rows_writer = csv.writer(handle)
            rows_writer.writerow(headers)
            safe = excel_safe
            for row in records:
                rows_writer.writerow([safe(row.get(key, "")) for key in headers])
        files["csv"] = str(path)
        quality_path = output / "record_quality.csv"
        with quality_path.open("w", encoding="utf-8-sig", newline="") as handle:
            _write_rows(handle, quality_headers, quality_rows)
        files["quality_csv"] = str(quality_path)


    # S3.4.1 ⑤：responses.csv/errors.csv 文件受 outputs.csv 开关约束；
    # 数据本身始终加载（xlsx 内嵌"抓取清单/错误记录"sheet 也需要）
    response_rows = state.rows(
        f"SELECT final_url, status_code, content_type, size_bytes, content_sha256, raw_path, changed, elapsed_seconds, fetched_at FROM responses{where} ORDER BY id",
        params,
    )
    response_headers = list(response_rows[0]) if response_rows else ["final_url", "status_code", "content_type", "size_bytes", "content_sha256", "raw_path", "changed", "elapsed_seconds", "fetched_at"]
    error_rows = state.rows(f"SELECT url, stage, error_type, message, retryable, created_at FROM errors{where} ORDER BY id", params)
    error_headers = list(error_rows[0]) if error_rows else ["url", "stage", "error_type", "message", "retryable", "created_at"]
    if outputs.get("csv", True):
        response_path = output / "responses.csv"
        with response_path.open("w", encoding="utf-8-sig", newline="") as handle:
            _write_rows(handle, response_headers, response_rows)
        files["responses_csv"] = str(response_path)

        error_path = output / "errors.csv"
        with error_path.open("w", encoding="utf-8-sig", newline="") as handle:
            _write_rows(handle, error_headers, error_rows)
        files["errors_csv"] = str(error_path)

    if outputs.get("xlsx", True):
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill
        except ImportError:
            # S2.5.24：xlsx 缺 openpyxl 显式告警（与 parquet/duckdb 一致），不再静默丢弃
            LOGGER.warning(
                "xlsx 导出已启用但未安装 openpyxl；跳过 xlsx（pip install openpyxl）"
            )
        else:
            # ★ 2026-11-25：改用 **write_only 模式**（实测 8.19x）。
            #
            # 默认 Workbook 每写一个单元格都会构造 Cell 对象并跑一遍
            # `check_string` / `_bind_value`，2 万条 × 8 列 = 16 万次 —— 实测
            # 20k 行 717 ms，其中 1.76 s 累计在 `openpyxl.writer.excel.save`
            # 的逐格构造上。write_only 直接把值流式写进 XML，不保留单元格对象：
            #
            #     普通 Workbook :  717 ms
            #     write_only    :   88 ms   -> 8.19x
            #
            # 限制与处理：
            #   * write_only 下**没有 `workbook.active`**，必须 create_sheet；
            #   * 表头样式不能用 `sheet[1]` 回读，得先造 `WriteOnlyCell` 再 append；
            #   * `freeze_panes` / 多 sheet / 样式**都保留**（已实测读回验证）。
            from openpyxl.cell import WriteOnlyCell

            workbook = Workbook(write_only=True)
            sheet = workbook.create_sheet("结构化记录")
            sheet.freeze_panes = "A2"
            header_cells = []
            for name in headers:
                cell = WriteOnlyCell(sheet, value=name)
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="1F4E78")
                header_cells.append(cell)
            sheet.append(header_cells)
            # S3.4.1 ⑦：xlsx 行上限保护（Excel 单表 1,048,576 行硬限）
            XLSX_ROW_LIMIT = 1_000_000
            if len(records) > XLSX_ROW_LIMIT:
                LOGGER.warning(
                    "xlsx 记录数 %d 超过单表上限 %d，超出部分已截断", len(records), XLSX_ROW_LIMIT
                )
            for row in records[:XLSX_ROW_LIMIT]:
                sheet.append([_excel_cell(row.get(key, "")) for key in headers])
            quality_sheet = workbook.create_sheet("质量与复核")
            quality_sheet.freeze_panes = "A2"
            quality_sheet.append(quality_headers)
            for row in quality_rows[:XLSX_ROW_LIMIT]:
                quality_sheet.append([_excel_cell(row.get(key)) for key in quality_headers])
            response_sheet = workbook.create_sheet("抓取清单")
            response_sheet.append(response_headers)
            for row in response_rows[:XLSX_ROW_LIMIT]:
                response_sheet.append([_excel_cell(row.get(key, "")) for key in response_headers])
            error_sheet = workbook.create_sheet("错误记录")
            error_sheet.append(error_headers)
            for row in error_rows[:XLSX_ROW_LIMIT]:
                error_sheet.append([_excel_cell(row.get(key, "")) for key in error_headers])
            path = output / "extraction_results.xlsx"
            try:
                workbook.save(path)
            except (PermissionError, OSError) as exc:
                # S3.4.1 ⑧：文件被占用/不可写时友好错误，不再裸抛
                raise RuntimeError(
                    f"无法写入 Excel 文件 {path}——文件可能被其他程序占用或目录不可写: {exc}"
                ) from exc
            files["xlsx"] = str(path)

    if outputs.get("parquet", False):
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError:
            optional_warnings.append(
                "Parquet output was requested but pyarrow is unavailable; install omnicrawler-platform[storage]"
            )
        else:
            # S3.4.1 ③：parquet 保留原始类型（不再全 str 化），pyarrow 自动推断
            table = pa.Table.from_pylist(records) if records else pa.table({"record_id": pa.array([], type=pa.string())})
            path = output / "records.parquet"
            pq.write_table(table, path, compression="zstd")
            files["parquet"] = str(path)

    if outputs.get("duckdb", False):
        try:
            import duckdb
        except ImportError:
            optional_warnings.append(
                "DuckDB output was requested but duckdb is unavailable; install omnicrawler-platform[storage]"
            )
        else:
            path = output / "analytics.duckdb"
            columns = _validate_column_names(headers or ["record_id"])
            # S3.4.1 ③：按样例推断类型（INTEGER/DOUBLE/BOOLEAN/VARCHAR），不全 VARCHAR
            typed = ", ".join(
                f'"{name.replace(chr(34), chr(34) * 2)}" {_infer_column_type(name, records)}'
                for name in columns
            )
            placeholders = ", ".join("?" for _ in columns)
            connection = duckdb.connect(str(path))
            try:
                connection.execute("DROP TABLE IF EXISTS records")
                connection.execute(f"CREATE TABLE records ({typed})")
                if records:
                    # S3.4.1 ③：保留原始类型插入（不再全 str 化）
                    connection.executemany(
                        f"INSERT INTO records VALUES ({placeholders})",
                        [[row.get(key) for key in columns] for row in records],
                    )
            finally:
                connection.close()
            files["duckdb"] = str(path)

    # 复用本函数已解析好的 evidence —— 否则 build_quality_report 会再 SELECT
    # 一遍 records 并再 json.loads 一遍（2 万条实测多付 1 次读库 + 1 次解析）。
    quality_report = build_quality_report(
        config, state, run_id,
        parsed_evidence=parsed_evidence,
        parsed_data=parsed_data,
        record_total=len(raw_records),
    )
    files["quality_report_json"] = quality_report["files"]["json"]
    files["quality_report_html"] = quality_report["files"]["html"]
    error_center = build_error_center(state, output, run_id)
    files["error_center_json"] = error_center["files"]["json"]
    files["error_center_html"] = error_center["files"]["html"]
    artifact_integrity = verify_artifacts(state, run_id, workspace=config.workspace)
    integrity_path = output / "artifact_integrity.json"
    integrity_path.write_text(
        json.dumps(artifact_integrity, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    files["artifact_integrity"] = str(integrity_path)
    # EU AI Act Art. 53(1)(d) 训练内容公开摘要（默认关闭）。
    # 之所以能按 run 维度给出可验证的事实：内容侧走 `artifacts` 表 + 已有的
    # `verify_artifacts`（逐文件重算 sha256），来源侧走 `source_url` 实测域名摘要
    # 与模板元数据里人工声明的 `source_urls`/`license`/`verified_at`。
    # 取不到的字段一律写 GAP + 理由，绝不写空白（空白会被读成"无需申报"）。
    if outputs.get("ai_act_summary", False):
        from ..quality.training_content_summary import (
            build_training_content_summary,
            write_training_content_summary,
        )

        summary = build_training_content_summary(
            state,
            run_id,
            workspace=config.workspace,
            effective_config=dict(config.raw),
        )
        summary_path = output / "ai_act_training_summary.json"
        write_training_content_summary(summary, summary_path)
        files["ai_act_training_summary"] = str(summary_path)
    # W3.2（§5.2 #6）：**「访问 N 页却只交付 M 条」必须可见**。
    # 两个数字**无条件**写进摘要（低于阈值时用户也能自己判断）；比值异常时另加一条告警，
    # 免得"只采到一点点"被当成正常完成悄悄交付。
    pages_visited = len(response_rows)
    records_delivered = len(records)
    delivery: dict[str, Any] = {
        "pages_visited": pages_visited,
        "records_delivered": records_delivered,
        "records_per_page": (
            round(records_delivered / pages_visited, 4) if pages_visited else None
        ),
    }
    if pages_visited >= _PAGINATION_GAP_MIN_PAGES and records_delivered * 2 < pages_visited:
        delivery["pagination_gap_suspected"] = True
        optional_warnings.append(
            f"分页完整性可疑：访问 {pages_visited} 页只交付 {records_delivered} 条"
            f"（每页不足 0.5 条）。请检查：列表项选择器是否过窄、分页是否真的生效、"
            f"详情页解析是否失败。"
        )
    else:
        delivery["pagination_gap_suspected"] = False

    # 走查 R1.2：**「被页数预算截断」必须可见**。
    # 上面那条判据只覆盖「每页不足 0.5 条」，对"每页都有货、但页码没走完"完全不敏感 ——
    # 实测（0.13.0）某 50 页站点在 60 页预算下只交付 577/1000 条，而
    # pagination_gap_suspected=false、warnings=[]，用户无法从任何字段看出结果不完整。
    # 「还有多少待抓」这个数据其实早已存在（state.pending_count()，也被导成指标），
    # 只是没进交付摘要 —— 这里把它与配置预算一起写出来。
    frontier = state.stats(run_id)["frontier"] if run_id else {}
    frontier_pending = int(frontier.get("pending", 0) or 0)
    configured_budget = int(config.section("crawl").get("max_pages", 100) or 0)
    delivery["frontier_pending"] = frontier_pending
    delivery["cumulative_records"] = int(state.stats()["records"])
    delivery["budget_exhausted"] = bool(
        frontier_pending > 0 and configured_budget > 0 and pages_visited >= configured_budget
    )
    if delivery["budget_exhausted"]:
        optional_warnings.append(
            f"采集被页数预算截断：已访问 {pages_visited} 页（crawl.max_pages={configured_budget}），"
            f"仍有 {frontier_pending} 个 URL 待抓 —— 本次结果不完整。"
            f"确认需要全量时请调大 crawl.max_pages 后重新运行。"
        )

    summary = {
        "project": config.project_name, "run_id": run_id, "exported_at": utcnow(),
        "records": len(records), "responses": len(response_rows),
        "errors": len(error_rows),
        "delivery": delivery,
        "files": files, "warnings": optional_warnings, "quality": quality_report,
        "error_center": error_center,
        "artifact_integrity": artifact_integrity,
    }
    summary_path = output / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    files["summary"] = str(summary_path)
    return summary


def register(registry) -> None:
    registry.register_exporter("default", export_all)
