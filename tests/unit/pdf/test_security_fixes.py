"""Phase 4 安全加固测试（D66 模板穿越 / F38 Selenium 回退 / C40 .env 权限）。"""

from __future__ import annotations

import pytest

from omnicrawler.pdfx.templates import (
    builtin_pdf_resource,
    resolve_builtin_pdf_reference,
)


def test_d66_normal_builtin_reference_resolves() -> None:
    resolved = resolve_builtin_pdf_reference("builtin:pdf/generic_template.yaml")
    assert resolved.is_file()
    assert resolved == builtin_pdf_resource("generic_template.yaml")


def test_d66_path_traversal_rejected() -> None:
    with pytest.raises(ValueError, match="越界"):
        resolve_builtin_pdf_reference("builtin:pdf/../../../etc/passwd")
    # 反斜杠穿越同样拒绝
    with pytest.raises(ValueError, match="越界"):
        resolve_builtin_pdf_reference("builtin:pdf/..\\..\\secret.yaml")


def test_d66_encoded_slash_is_not_a_traversal_vector() -> None:
    """%2f 是字面字符（Path 不解析），不会穿越；按不存在处理即可（不抛越界）。"""
    with pytest.raises((ValueError, FileNotFoundError)):
        resolve_builtin_pdf_reference("builtin:pdf/..%2f..%2fx.yaml")


def test_c40_env_file_permissions(tmp_path) -> None:
    """C40：save_ai_env 写入后收紧权限（POSIX 0600；Windows chmod 部分生效跳过严格断言）。"""
    import os
    import stat

    from omnicrawler.core.ai_env import parse_env_file, save_ai_env

    project = tmp_path / "proj"
    project.mkdir()
    save_ai_env({"OMNICRAWL_AI_MODEL": "m"}, project_root=project)
    path = project / ".env"
    assert parse_env_file(path)["OMNICRAWL_AI_MODEL"] == "m"
    if os.name != "nt":
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode & 0o077 == 0  # 组/其他无任何权限
