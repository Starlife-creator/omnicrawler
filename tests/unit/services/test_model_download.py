"""条目 4：模型一律从上游下载——本项目不托管、不镜像、不校验模型字节。

本文件钉住三件事，每件都是**防止别人（或未来的我）走错方向**的护栏：

1. **不自己发 HTTP**：模型字节必须由 PaddleX 携带的下载器取得。
   本项目若自己写下载器，就会引入第二套网络行为，与"市场下载复用任务抓取
   同一条出口"的既有纪律冲突。判据是**源码里不出现模型下载用的 urllib/requests**。
2. **源可切换且会回落**：未知源不得让整个下载崩掉，但必须留痕。
3. **就绪判据是 `inference.pdiparams`**：目录存在不等于可用——半截下载的目录
   会让用户以为装好了，这正是要防的"看起来成功"。

**责任边界**：本模块不为模型字节的内容完整性背书（那是上游的事）。
所以这里**不测**哈希、不测签名——测了就等于宣称我们在校验。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MODULE = _REPO_ROOT / "src" / "omnicrawler" / "services" / "model_download.py"


def test_module_does_not_implement_its_own_http_download() -> None:
    """模型下载不得由本项目自己发 HTTP 请求。

    允许 import 上游库（paddleocr/paddlex），但不允许本模块直接
    使用 urllib / requests / httpx 去拉模型字节。
    """
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    forbidden = {"urllib", "requests", "httpx", "aiohttp", "socket"}
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offenders += [a.name for a in node.names if a.name.split(".")[0] in forbidden]
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in forbidden:
                offenders.append(node.module)
    assert offenders == [], (
        f"model_download.py 自己实现了网络访问：{offenders}。"
        "模型必须由 PaddleX 自带的下载器取得（PADDLE_PDX_MODEL_SOURCE），"
        "否则会形成第二套网络行为。"
    )


def test_unknown_source_falls_back_but_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    """未知源回落默认值 —— 但必须留痕，不能静默。"""
    from omnicrawler.services import model_download

    with caplog.at_level("WARNING"):
        resolved = model_download.normalize_source("no-such-source")
    assert resolved == model_download.DEFAULT_MODEL_SOURCE
    assert any("未知" in record.message or "回落" in record.message
               for record in caplog.records), (
        "源回落必须写日志——否则用户永远不知道自己填错了源"
    )


def test_known_source_is_preserved() -> None:
    from omnicrawler.services import model_download

    for source in model_download.MODEL_SOURCES:
        assert model_download.normalize_source(source) == source


def test_default_source_is_not_huggingface() -> None:
    """默认源不能是 huggingface —— 本项目实测其直连 SSL 失败。"""
    from omnicrawler.services import model_download

    assert model_download.DEFAULT_MODEL_SOURCE != "huggingface", (
        "本机与部分用户网络下 huggingface.co 直连不可达；"
        "默认源选它会让首次使用必然失败"
    )


def test_incomplete_model_directory_is_not_ready(tmp_path: Path) -> None:
    """目录存在但缺 inference.pdiparams ⇒ 不算就绪（与 capabilities F41 同口径）。"""
    from omnicrawler.services import model_download

    official = tmp_path / "official_models"
    (official / "PP-OCRv5_server_det").mkdir(parents=True)
    (official / "PP-OCRv5_server_det" / "config.json").write_text("{}", encoding="utf-8")
    assert model_download.downloaded_models(tmp_path) == [], (
        "半截下载目录被判为就绪——用户会以为模型装好了"
    )

    (official / "PP-OCRv5_server_rec").mkdir(parents=True)
    (official / "PP-OCRv5_server_rec" / "inference.pdiparams").write_bytes(b"w")
    assert model_download.downloaded_models(tmp_path) == ["PP-OCRv5_server_rec"]


def test_probe_never_downloads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """probe 必须是纯只读：它不得触发 PaddleX 构造（那会联网下载）。"""
    from omnicrawler.services import model_download

    monkeypatch.setenv("PADDLE_PDX_CACHE_HOME", str(tmp_path))

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("probe 触发了下载路径")

    # 若 probe 不慎构造 pipeline，这里会炸。
    monkeypatch.setattr("paddleocr.PPStructureV3", _boom, raising=False)
    report = model_download.probe()
    assert report["ready"] is False
    assert report["models"] == []


def test_download_reports_unavailable_without_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有 PaddleOCR 运行时 ⇒ unavailable（预期内降级），不是 failed。"""
    import builtins

    from omnicrawler.services import model_download

    real_import = builtins.__import__

    def _fake_import(name: str, *args: object, **kwargs: object):
        if name == "paddleocr":
            raise ImportError("No module named 'paddleocr'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    result = model_download.download()
    assert result["status"] == "unavailable", result
    assert result["reason"], "必须给出用户能照做的下一步"
    assert "组件" in result["reason"] or "Full" in result["reason"]
