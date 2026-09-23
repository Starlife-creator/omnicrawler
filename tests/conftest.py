"""测试套件共享 fixtures（P0-1 根因修复 + P1-14 sys.path 加成）。

- 固定 PYTHONHASHSEED：CI runner 熵不足时，被 spawn 的子进程（含
  IsolatedPluginRunner 沙箱）Python 解释器哈希随机化初始化偶发失败
  （_Py_HashRandomization_Init）。显式设种子可让 Python 跳过 OS 熵读取，
  行为完全确定。setdefault 尊重 CI/开发者已有的显式设置。
- sys.path 加入仓库根：多处测试 `from tools.* import` 依赖 repo_root
  在 sys.path。`python -m pytest` 时 cwd 在 sys.path 所以恰好可用；
  裸 `pytest` 时缺失会导致 collection error（P1-14）。
"""
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault("PYTHONHASHSEED", "42")


@pytest.fixture(autouse=True)
def _isolated_secret_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """SecretsStore 缺省路径隔离（U5 起 session 链路会在测试中触达默认密钥库）。

    ★ 若不隔离：测试会读/写**真实**的 ``~/.omnicrawler/secrets.bin``，且在
    keyring 不可用的 CI runner 上直接 SecretsStoreError（U5 首轮 CI 三平台
    test 矩阵红光的根因）。只设路径与主口令，**不动** OMNICRAWL_KEYRING_DISABLE
    —— 该变量会覆盖注入的 keyring_api，破坏既有密钥测试；CI 上 keyring
    不可用时 SecretsStore 自行走口令派生兜底。个别测试需要别的环境时，
    在用例内 monkeypatch.setenv/delenv 即可覆盖本 fixture。
    """
    monkeypatch.setenv("OMNICRAWL_SECRET_STORE_PATH", str(tmp_path / "secrets.bin"))
    monkeypatch.setenv("OMNICRAWL_MASTER_PASSWORD", "test-only-master-password")


@pytest.fixture(autouse=True)
def _sys_path_snapshot():
    """B13-004：每个测试结束后还原 sys.path。

    部分测试在模块级 `sys.path.insert(0, .../src)` 加载构建工具（build_runtime /
    cli_pipeline / cross_platform fixes）。该模块级 insert 是一次性全局副作用
    （无 exec/注入，仅为 `from tools.*` 可复现构建断言），fixture 无法 undo 它；
    此快照至少保证测试运行过程中任何动态 insert 不跨测试累积，污染后续
    测试的 import 解析顺序。
    """
    snapshot = list(sys.path)
    yield
    sys.path[:] = snapshot


@pytest.fixture(autouse=True)
def _restore_i18n_language():
    """隔离 i18n 的全局语言状态（它是进程级量，不还原就会跨测试泄漏）。

    实测：`tests/unit/gui/test_i18n_gate.py` 会调用 `set_language("en_US")` 而不还原，
    于是同一进程里后跑的 `tests/integration/template/test_simple_experience.py`
    断言中文文案时失败——测试结果**取决于先跑了哪些目录**。
    这里按测试快照并还原，让套件对运行顺序不敏感。
    """
    try:
        from omnicrawler import i18n
    except ImportError:  # pragma: no cover - i18n 不可用时不影响其它测试
        yield
        return
    previous = i18n.get_current_language()
    try:
        yield
    finally:
        if isinstance(previous, str) and previous:
            i18n.set_language(previous)
