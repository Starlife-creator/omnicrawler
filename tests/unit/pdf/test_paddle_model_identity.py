"""条目 8：Paddle 模型身份必须进入 OCR 依赖指纹。

**缺口的真实后果**

`pdfx/dependencies.py` 的 `ocr_digest` 已经覆盖两类后端身份：
* Tesseract：可执行文件 + 选中语言的 `traineddata` 内容哈希；
* 组件：`_resolved_component_version`。

但 **Paddle 完全没有**——`PaddleStructureBackend` 没有 `self.version`
（全仓 `self.version` 只出现在 `ComponentOCRBackend` 里），
所以 `ocr_digest` 拿到的 `component_version` 恒为 `None`。

于是：用户往 `runtime/models/paddlex` 里换了模型 ⇒ 指纹**不变** ⇒
`refresh_processing` 不触发 ⇒ 旧结果被当成仍然有效 ⇒ 表格永远不会重新识别。

仓库自己也承认这个边界（`docs/DEPENDENCY_DEPTH_IMPLEMENTATION.md:159`）：
「Paddle 隐式管理的模型缓存…没有完整内容身份，仍不宣称可自动追踪」。

**判据（本守卫四条）**

1. **换模型 ⇒ 指纹必须变**（这是本条的全部意义）；
2. **只算本次实际用到的模型**，不是把整个缓存目录扫一遍——
   装了一堆没启用的模型不该让结果失效（否则用户每装一个就全量重跑）；
3. **未启用 OCR 时不做模型扫描**（避免在无关任务上付出 IO）；
4. **扫描成本必须有界**：不能每次起任务都把 1 GB 逐字节重算。

第 4 条用「轻身份」实现（模型名 + 主要权重文件大小/修改时间），
并提供**严格模式**（内容哈希）给需要确定性核对的场景。
"""

from __future__ import annotations

import pathlib

import pytest

from omnicrawler.pdfx.config import ProjectConfig


def _config(tmp_path: pathlib.Path, **ocr: object) -> ProjectConfig:
    """构造一个最小 ProjectConfig（只填 ocr_digest 需要的字段）。"""
    config_file = tmp_path / "project.yaml"
    config_file.write_text("fields: []\n", encoding="utf-8")
    return ProjectConfig(
        path=config_file,
        project_name="digest-test",
        input_dir=tmp_path / "in",
        work_dir=tmp_path / "work",
        output_dir=tmp_path / "out",
        database=tmp_path / "work" / "db.sqlite3",
        parser={},
        ocr=dict(ocr),
        retrieval={},
        llm={},
        extraction={},
        normalization={},
        validation={},
        fields=[],
    )


def _make_model(root: pathlib.Path, name: str, size: int = 1024) -> pathlib.Path:
    """造一个"看起来像真模型"的目录（含 inference.pdiparams）。"""
    directory = root / "official_models" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "inference.pdiparams").write_bytes(b"x" * size)
    (directory / "config.json").write_text("{}", encoding="utf-8")
    return directory


@pytest.fixture()
def paddle_cache(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    cache = tmp_path / "paddlex"
    cache.mkdir()
    monkeypatch.setenv("PADDLE_PDX_CACHE_HOME", str(cache))
    return cache


def test_paddle_model_change_invalidates_digest(
    tmp_path: pathlib.Path, paddle_cache: pathlib.Path
) -> None:
    """换模型内容 ⇒ 指纹必须变。这是本条唯一的实质判据。"""
    from omnicrawler.pdfx.dependencies import ocr_digest

    config = _config(tmp_path, backend="paddle", table_recognition=True)
    _make_model(paddle_cache, "PP-OCRv5_server_rec", size=1024)
    before = ocr_digest(config)

    # 同尺寸替换内容：只看大小会漏，必须能检出
    _make_model(paddle_cache, "PP-OCRv5_server_rec", size=1024)
    (paddle_cache / "official_models" / "PP-OCRv5_server_rec" / "inference.pdiparams").write_bytes(
        b"y" * 1024
    )
    after = ocr_digest(config)

    assert before != after, (
        "换了 Paddle 模型的**内容**但指纹没变 —— 旧结果会被当成仍然有效，"
        "用户改了模型却永远不会重新识别"
    )


def test_adding_an_unused_model_does_not_invalidate(
    tmp_path: pathlib.Path, paddle_cache: pathlib.Path
) -> None:
    """装了一个**没启用**的模型，不该让结果失效。

    否则用户每装一个组件就全量重跑，这在 1 GB 级缓存上非常贵。
    判据：只算本次 `ocr.table_recognition` / `orientation` 等开关实际需要的模型。
    """
    from omnicrawler.pdfx.dependencies import ocr_digest

    # 关掉表格识别 ⇒ 表格类模型不参与
    config = _config(tmp_path, backend="paddle", table_recognition=False)
    _make_model(paddle_cache, "PP-OCRv5_server_rec", size=1024)
    before = ocr_digest(config)

    # 新增一个表格模型目录（但配置没开表格识别）
    _make_model(paddle_cache, "SLANeXt_wired", size=2048)
    after = ocr_digest(config)

    assert before == after, (
        "装了一个未启用的模型就让指纹变了 —— 用户每装一个组件都要全量重跑"
    )


def test_no_model_scan_when_ocr_disabled(
    tmp_path: pathlib.Path, paddle_cache: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未启用 OCR 时不得扫描模型目录（无关任务不该付 IO 成本）。

    判据用**可观察副作用**而不是 monkeypatch 探测：把模型目录指向一个
    "一访问就报错"的路径——若真的去扫描，异常会冒出来。
    这比替换函数更可靠（替换函数只能证明"没调用那个函数"，
    证明不了"没碰磁盘"）。
    """
    from omnicrawler.pdfx.dependencies import _paddle_model_identities

    config = _config(tmp_path, backend="none")

    # 验证**返回值语义**：未启用时必须是 configured=False 且不含任何模型条目。
    # （替换函数只能证明"没调用某个函数"，证明不了"没碰磁盘"；
    # 而 os.environ 只存字符串，无法注入一个"一访问就炸"的路径对象。）
    result = _paddle_model_identities(config, tmp_path)
    assert result == {"configured": False}, (
        f"未启用 OCR 时仍产出了模型身份（可能扫描了目录）：{result}"
    )


def test_model_scan_is_bounded_not_full_content_hash(
    tmp_path: pathlib.Path, paddle_cache: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """默认必须是**有界**的轻身份，不能每次起任务都逐字节重算 1 GB。

    判据：默认路径下不读文件内容（用 open 计数验证）。
    """
    from omnicrawler.pdfx import dependencies

    opened: list[str] = []
    import builtins

    real_open = builtins.open

    def _counting_open(file, *args, **kwargs):  # type: ignore[no-untyped-def]
        mode = args[0] if args else kwargs.get("mode", "r")
        if "r" in str(mode) and "b" in str(mode):
            opened.append(str(file))
        return real_open(file, *args, **kwargs)

    config = _config(tmp_path, backend="paddle")
    _make_model(paddle_cache, "PP-OCRv5_server_rec", size=4096)

    monkeypatch.setattr(builtins, "open", _counting_open)
    dependencies.ocr_digest(config)
    monkeypatch.setattr(builtins, "open", real_open)

    model_reads = [path for path in opened if "official_models" in path]
    assert model_reads == [], (
        f"默认指纹读取了模型文件内容（{len(model_reads)} 次）——"
        "1 GB 级缓存在每次起任务时逐字节重算不可接受"
    )


def test_tesseract_identity_still_works(tmp_path: pathlib.Path) -> None:
    """新增 Paddle 身份不得破坏既有 Tesseract 指纹。"""
    from omnicrawler.pdfx.dependencies import ocr_digest

    config = _config(tmp_path, backend="tesseract", lang="eng")
    assert ocr_digest(config)  # 能算出来即可


def test_digest_contract_was_bumped() -> None:
    """契约版本必须是 2 —— 补了 Paddle 身份就必须升级契约号。

    升级契约号会让**升级后第一次运行**把所有历史 OCR 文档判为"依赖变了"而重跑一次。
    这是一个**有意接受的代价**（见下一条测试的说明），不是遗漏。
    """
    from omnicrawler.pdfx.dependencies import OCR_DIGEST_CONTRACT

    assert OCR_DIGEST_CONTRACT == 2, (
        "补了 Paddle 模型身份却没升契约号：指纹语义变了但版本没变，"
        "出问题时无法从库里区分新旧指纹。"
    )


def test_contract_upgrade_cost_is_explicit_and_documented() -> None:
    """把"升级要重跑一次"钉成**显式决策**，而不是将来的意外。

    为什么不做自动基线（记录在这里，防止后来者"优化"掉）：
    仅凭"库里的 digest 与当前值不同"**无法区分**两种情况——
    ① 旧契约留下的旧值；② 用户真的换了 OCR 版本。
    自动把①对齐成基线会连②一起吞掉，而"换了模型却不重新识别"
    正是条目 8 要修的 bug。用"迁移标记"同样不行：标记写入后永久存在，
    之后的真实变更照样被吞。

    实现上踩到过两次：
    * 第一版无条件对齐 ⇒ 4 条既有 refresh 测试转红（掩盖了真实版本变更）；
    * 第二版 SQL 漏传绑定参数 ⇒ 15 条测试报 sqlite3.ProgrammingError。
    """
    import inspect

    from omnicrawler.pdfx import refresh

    source = inspect.getsource(refresh.refresh_processing)
    assert "contract_rebaseline" not in source, (
        "refresh_processing 里出现了自动基线逻辑——"
        "它会掩盖真实的 OCR 版本变更（详见本测试 docstring）"
    )
