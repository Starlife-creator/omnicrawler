"""recipe 装配链路：从上游拉字节、本地组装、失败不留半成品。

## 为什么需要这组测试

维护者的分工是「**下载从上游拉，需要组装就在本地组装，本项目只维护组装方式**」
（`services/component_recipe.py` 开头）。据此，市场只托管几十 KB 的声明式
recipe，不托管几百 MB 的模型字节。

`component_recipe` 引擎早已写好，但 `install_from_recipe()` 此前**只有单元
测试在用**——没有任何生产入口，等于链路断在最后一米。本文件钉住新接的入口
`services/component_install.py` 与 CLI 行为。

## 测试用本地假上游

**不下载真实模型**：模型动辄几百 MB 到 1 GB+，验证链路不需要真实字节。
起一个本地 HTTP 服务托管几十字节的假 zip，走**完整生产代码路径**。

## 判据重点

* 装配成功时入口存在、内容正确；
* **哈希不符必须报"哈希不符"**——不能伪装成别的错误（实测踩过：
  安装根目录不存在时抛 `FileNotFoundError`，看起来像哈希校验失败）；
* 可执行钩子（`post_install`）被拒；
* `..` 路径穿越被拒；
* CLI `install-recipe` 不加 `--download` 时**一个字节都不下载**。
"""

from __future__ import annotations

import hashlib
import io
import json
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from omnicrawler.services.component_install import (
    RecipeInstallError,
    describe_recipe,
    install_recipe_file,
)

_PAYLOAD_TEXT = b"fake-traineddata-bytes" * 8


def _build_zip(entry_name: str = "chi_sim.traineddata") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(entry_name, _PAYLOAD_TEXT)
    return buffer.getvalue()


class _UpstreamHandler(BaseHTTPRequestHandler):
    payload: bytes = b""

    def do_GET(self) -> None:  # noqa: N802 - http.server 约定名
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, *args: object) -> None:
        return


@pytest.fixture
def fake_upstream():
    """起一个托管假构件的本地 HTTP 服务，返回 (url, payload)。"""
    payload = _build_zip()
    _UpstreamHandler.payload = payload
    server = ThreadingHTTPServer(("127.0.0.1", 0), _UpstreamHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}/artifact.zip", payload
    finally:
        server.shutdown()


def _local_downloader(url: str, destination: Path) -> None:
    import urllib.request

    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=10) as response:
        destination.write_bytes(response.read())


def _recipe(url: str, digest: str, size: int) -> dict:
    return {
        "schema": 1,
        "name": "fake-ocr-pack",
        "version": "0.0.1",
        "description": "测试用假组件",
        "platforms": ["windows", "linux", "darwin"],
        "architectures": ["x86_64"],
        "python_abi": [],
        "artifacts": [
            {
                "url": url,
                "sha256": digest,
                "size": size,
                "extract_to": "tessdata",
                "include": ["chi_sim.traineddata"],
            }
        ],
        "entry": {
            "path": "tessdata/chi_sim.traineddata",
            "protocol": "ocr-file-v1",
            "engine": "tesseract",
        },
    }


def _write(tmp_path: Path, name: str, data: dict) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def test_recipe_assembles_from_upstream(tmp_path: Path, fake_upstream) -> None:
    """从上游拉字节并在本地组装，入口与内容都正确。"""
    url, payload = fake_upstream
    digest = hashlib.sha256(payload).hexdigest()
    recipe_path = _write(tmp_path, "recipe.json", _recipe(url, digest, len(payload)))

    summary = install_recipe_file(
        recipe_path, tmp_path / "components", downloader=_local_downloader
    )

    entry = Path(summary["entry"])
    assert entry.is_file(), "装配完成但入口不存在"
    assert entry.read_bytes() == _PAYLOAD_TEXT, "装配内容与上游字节不一致"
    assert Path(summary["path"]).is_dir()
    assert summary["name"] == "fake-ocr-pack"
    assert summary["recipe_digest"], "摘要应带 recipe_digest，供市场核对是否审查过同一份"


def test_hash_mismatch_reports_hash_error_not_side_effect(
    tmp_path: Path, fake_upstream
) -> None:
    """哈希不符必须报**哈希不符**。

    实测踩过：安装根目录不存在时 `install_from_recipe` 抛
    `FileNotFoundError`（暂存区建不出来），错误信息指向 `.recipe-xxxx`，
    看起来像装配问题而不是哈希校验失败——诊断成本极高。
    本测试用**尚不存在的**安装根，确保错误仍是哈希错误。
    """
    url, payload = fake_upstream
    recipe_path = _write(tmp_path, "recipe.json", _recipe(url, "f" * 64, len(payload)))

    with pytest.raises(RecipeInstallError) as excinfo:
        install_recipe_file(
            recipe_path,
            tmp_path / "not-yet-existing-root",  # 刻意不存在
            downloader=_local_downloader,
        )

    message = str(excinfo.value)
    assert "哈希不符" in message, (
        f"应报哈希不符，实际：{message[:200]}"
    )
    assert "FileNotFoundError" not in message

    # 失败后不得留下装配目录（两阶段设计：暂存区失败即整体放弃）
    root = tmp_path / "not-yet-existing-root"
    leftovers = [p.name for p in root.iterdir()] if root.is_dir() else []
    assert not leftovers, f"失败后留了半成品：{leftovers}"


def test_executable_hook_is_rejected(tmp_path: Path, fake_upstream) -> None:
    """`post_install` 之类的可执行钩子必须被拒 —— recipe 只能是数据。"""
    url, payload = fake_upstream
    digest = hashlib.sha256(payload).hexdigest()
    recipe = _recipe(url, digest, len(payload))
    recipe["post_install"] = "rm -rf /"
    recipe_path = _write(tmp_path, "recipe.json", recipe)

    with pytest.raises(RecipeInstallError) as excinfo:
        install_recipe_file(recipe_path, tmp_path / "c", downloader=_local_downloader)
    assert "post_install" in str(excinfo.value)


def test_path_traversal_is_rejected(tmp_path: Path, fake_upstream) -> None:
    """`extract_to` 含 `..` 必须被拒。"""
    url, payload = fake_upstream
    digest = hashlib.sha256(payload).hexdigest()
    recipe = _recipe(url, digest, len(payload))
    recipe["artifacts"][0]["extract_to"] = "../escape"
    recipe_path = _write(tmp_path, "recipe.json", recipe)

    with pytest.raises(RecipeInstallError) as excinfo:
        install_recipe_file(recipe_path, tmp_path / "c", downloader=_local_downloader)
    assert "路径穿越" in str(excinfo.value) or ".." in str(excinfo.value)


def test_describe_recipe_does_not_touch_network(tmp_path: Path) -> None:
    """`describe_recipe` 必须只读：不下载、不落盘。

    用**不可达的 URL**——若它试图联网，会超时或报错；
    正常情况应直接返回声明摘要。
    """
    recipe_path = _write(
        tmp_path,
        "recipe.json",
        _recipe("http://127.0.0.1:1/never.zip", "a" * 64, 1234),
    )

    plan = describe_recipe(recipe_path)
    assert plan["name"] == "fake-ocr-pack"
    assert plan["declared_total_bytes"] == 1234
    assert plan["artifacts"][0]["url"].endswith("/never.zip")
    assert not (tmp_path / "components").exists(), "describe_recipe 不该创建任何目录"


def test_cli_install_recipe_without_download_writes_nothing(tmp_path: Path) -> None:
    """CLI `install-recipe` 不加 `--download` 时一个字节都不下载。

    与 `uninstall`/`rollback` 同一条「显式动作」纪律：从上游拉字节是
    显式的对外动作，必须显式确认。
    """
    import argparse

    from omnicrawler.cli import _handlers

    recipe_path = _write(
        tmp_path,
        "recipe.json",
        _recipe("http://127.0.0.1:1/never.zip", "a" * 64, 999),
    )
    component_root = tmp_path / "components"

    args = argparse.Namespace(
        action="install-recipe",
        recipe=str(recipe_path),
        component_root=str(component_root),
        download=False,
    )
    _handlers._run_component_recipe(args)

    assert not component_root.exists(), "未加 --download 却创建了安装目录"
