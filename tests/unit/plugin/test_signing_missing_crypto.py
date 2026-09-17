"""`cryptography` 缺席时，插件签名验证必须抛出**清晰的 ImportError**（不是 NameError）。

## 这条守的是"判据有没有资格说话"

`src/omnicrawler/plugins/signing.py` 用惰性导入：`_ensure_crypto()` 在缺 `cryptography` 时
**故意**抛一句可执行的提示（"请运行 pip install 'omnicrawler-platform[security]'"）。
但 `InvalidSignature` 原先只是一句**纯注解**（`InvalidSignature: Any`，没有绑定值），
而 `verify_bytes()` 里写的是 `except (InvalidSignature, ValueError, TypeError)`
⇒ 缺库时 `_ensure_crypto()` 先抛 ImportError，`except` 子句求值时名字**未绑定**
⇒ 抛 `NameError`，**把那句清晰的提示完全吞掉**。

实测暴露场景（W3.4 的 `docker run --network none`，镜像只装 `[html,async-http,streams]`）：
日志里只有 `NameError: name 'InvalidSignature' is not defined`，
真正的原因（缺 security extra）根本看不到。

★ 用**子进程**验证：`cryptography` 若已被本进程其它用例导入，模块缓存会让屏蔽失效（假通过）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]

_PROBE = """
import sys
class Blocker:
    def find_spec(self, name, path=None, target=None):
        if name == "cryptography" or name.startswith("cryptography."):
            raise ImportError("simulated: no cryptography (container lacks [security] extra)")
        return None
sys.meta_path.insert(0, Blocker())
sys.path.insert(0, {src!r})

# 前提校验：屏蔽必须真的生效，否则本用例在空转
try:
    import cryptography  # noqa: F401
except ImportError:
    pass
else:
    print("BLOCKER_FAILED")
    raise SystemExit(3)

from omnicrawler.plugins import signing
try:
    signing.verify_bytes(b"payload", b"signature", "-----BEGIN PUBLIC KEY-----")
except ImportError as exc:
    print("IMPORTERROR:", exc)
except NameError as exc:
    print("NAMEERROR:", exc)
except Exception as exc:  # noqa: BLE001
    print(type(exc).__name__ + ":", exc)
else:
    print("NOERROR")
"""


def test_missing_cryptography_raises_a_clear_import_error() -> None:
    completed = subprocess.run(  # noqa: S603 - argv is built from literals + sys.executable
        [sys.executable, "-c", _PROBE.format(src=str(_REPO_ROOT / "src"))],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(_REPO_ROOT),
        timeout=120,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    assert "BLOCKER_FAILED" not in output, (
        "屏蔽 cryptography 失败（模块已被缓存？）⇒ 本用例在空转，结论无效"
    )
    assert "IMPORTERROR:" in output, (
        "缺 cryptography 时必须抛出**清晰的 ImportError**（内含补齐命令）；"
        f"实际输出：{output[-800:]}"
    )
    assert "NAMEERROR:" not in output, (
        "出现 NameError ⇒ `except` 子句里的名字未绑定，把真实的缺库原因吞掉了："
        f"{output[-800:]}"
    )
    assert "security" in output, (
        f"ImportError 的文案里应给出补齐路径（[security] extra）；实际输出：{output[-800:]}"
    )
