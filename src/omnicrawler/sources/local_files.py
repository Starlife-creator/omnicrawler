"""Explicit local imports, separate from HTTP fetchers and network policy."""
from __future__ import annotations

import mimetypes
import time
from pathlib import Path
from typing import Any

from ..core.config import AppConfig
from ..core.models import CrawlRequest, FetchResult

MAX_LOCAL_BYTES = 16 * 1024 * 1024


def declared_files(config: AppConfig) -> dict[str, Path]:
    values = config.section("source").get("local_files")
    if not isinstance(values, list) or not values or len(values) > 1000:
        raise ValueError("source.local_files 必须明确列出 1 到 1000 个配置目录内的文件")
    value = config.section("source").get("local_root")
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ValueError("source.local_root 必须是明确的本地输入目录")
    root = (config.path.parent / value).absolute() if value else config.path.parent.resolve()
    if str(root).startswith(("\\\\", "//")):
        raise PermissionError("本地输入目录不允许网络共享路径")
    paths = {}
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("本地文件路径不能为空")
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = root / path
        absolute = path.absolute()
        if str(absolute).startswith(("\\\\", "//")) or not absolute.is_relative_to(root):
            raise PermissionError("本地文件必须位于配置文件目录内")
        for part in (absolute, *absolute.parents):
            if part.is_symlink() or getattr(part, "is_junction", lambda: False)():
                raise PermissionError("本地导入不允许符号链接或目录联接")
        resolved = absolute.resolve()
        if not resolved.is_relative_to(root.resolve()):
            raise PermissionError("本地文件不能越过配置文件目录或明确选择的输入目录")
        paths[resolved.as_uri()] = resolved
    return paths


def seed(config: AppConfig) -> list[CrawlRequest]:
    files = declared_files(config)
    selected: Any = config.section("source").get("seeds") or list(files)
    if not isinstance(selected, list) or any(not isinstance(url, str) or url not in files for url in selected):
        raise PermissionError("本地种子必须是 source.local_files 中明确列出的文件")
    return [CrawlRequest(url, meta={"root_url": url, "source_kind": "file", "local_import": True}) for url in selected]


def fetch(config: AppConfig, request: CrawlRequest) -> FetchResult:
    started = time.monotonic()
    files = declared_files(config)
    if request.url not in files or request.method != "GET" or request.render or request.body or request.headers:
        raise PermissionError("本地导入只允许读取明确列出的原始文件，不支持请求头、请求体或浏览器渲染")
    path = files[request.url]
    if not path.is_file() or path.stat().st_size > MAX_LOCAL_BYTES:
        raise ValueError("本地导入文件不存在或超过 16 MiB；大型文档请使用 PDF 工作台")
    with path.open("rb") as stream:
        body = stream.read(MAX_LOCAL_BYTES + 1)
    if len(body) > MAX_LOCAL_BYTES:
        raise ValueError("读取期间本地文件超过 16 MiB")
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FetchResult(request, request.url, 200, {"content-type": content_type}, body,
                       time.monotonic() - started, {"local_import": True, "declared_path": str(path)})
