"""`fix_windows_integrity.ps1` 的存在性与关键判据。

为什么要有这个文件：2026-11-25 用户实测到大量 `[Errno 13] Permission denied:`
（组件注册表锁、日志文件），而目录 ACL 明明有「完全控制」。

真因：工作目录带**低完整性标签**（Windows 强制完整性控制，不是普通 ACL），
于是 `.venv\\Scripts\\python.exe` 以 Low 完整性运行；用户目录是 Medium，
低不能写高 ⇒ 所有写入报 13。实测矩阵（同一脚本，两个 exe）：

```
Low 完整性 python 写：项目目录 OK / 数据目录 PermissionError / TEMP PermissionError
Medium 完整性 python 写：全部 OK
```

本文件只钉住「修复脚本存在且判据正确」，不跑真正的 icacls（那要改系统状态）。
"""

from __future__ import annotations

import re
from pathlib import Path


def _script() -> Path:
    return Path(__file__).resolve().parents[3] / "tools" / "portable" / "fix_windows_integrity.ps1"


def test_repair_script_exists() -> None:
    assert _script().is_file(), f"缺少修复脚本：{_script()}"


def test_script_is_utf8_with_bom() -> None:
    """Windows PowerShell 5.1 按 ANSI 读无 BOM 文件 ⇒ 中文乱码导致解析失败。"""
    raw = _script().read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "脚本必须以 UTF-8 BOM 开头"


def test_script_probes_instead_of_guessing() -> None:
    """必须**实测**写一次，而不是只看 ACL —— ACL 会说可写但实际写不进。"""
    text = _script().read_text(encoding="utf-8-sig")
    assert "write_bytes" in text, "脚本没有真的写探针文件"
    assert "WRITABLE" in text
    assert "DENIED" in text


def test_script_reports_the_integrity_level_it_found() -> None:
    text = _script().read_text(encoding="utf-8-sig")
    assert "Mandatory Label" in text
    assert "Low Mandatory" in text


def test_script_repairs_with_setintegritylevel() -> None:
    """修复动作必须是设置完整性标签，而不是改 ACL 或删数据。"""
    text = _script().read_text(encoding="utf-8-sig")
    assert "/setintegritylevel" in text
    assert "(OI)(CI)M" in text
    # 绝不能出现危险动作：删数据目录 / 改所有者 / 重置 ACL
    for forbidden in ("/reset", "/setowner", "Remove-Item $ProjectRoot", "rmdir /s"):
        assert forbidden not in text, f"脚本出现危险动作：{forbidden}"


def test_script_verifies_after_repair() -> None:
    """改完必须复检，不能只报「已修复」。"""
    text = _script().read_text(encoding="utf-8-sig")
    assert "复检" in text
    assert text.count("Invoke-WriteProbe") >= 2, "恢复后没有重新探测"


def test_script_has_no_temp_file_lifecycle_bug() -> None:
    """探针临时文件必须在函数内成对创建/删除（早先版本删了又引用）。"""
    text = _script().read_text(encoding="utf-8-sig")
    assert "GetTempFileName" in text
    assert re.search(r"finally\s*\{[^}]*Remove-Item", text), "探针临时文件没有 finally 清理"


def test_script_avoids_param_default_psscriptroot() -> None:
    """`$PSScriptRoot` 写在 param 默认值里实测报 InvalidData，必须放脚本体内。"""
    text = _script().read_text(encoding="utf-8-sig")
    assert "$ProjectRoot = (Split-Path" not in text, (
        "$PSScriptRoot 被用作 param 默认值 —— 实测在 Windows PowerShell 5.1 上会报 InvalidData"
    )
