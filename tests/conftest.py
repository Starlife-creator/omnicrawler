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

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault("PYTHONHASHSEED", "42")
