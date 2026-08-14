"""测试套件共享 fixtures（P0-1 根因修复配套）。

固定 PYTHONHASHSEED：CI runner 熵不足时，被 spawn 的子进程（含
IsolatedPluginRunner 沙箱）Python 解释器哈希随机化初始化偶发失败
（_Py_HashRandomization_Init）。显式设种子可让 Python 跳过 OS 熵读取，
行为完全确定。setdefault 尊重 CI/开发者已有的显式设置。
"""
import os

os.environ.setdefault("PYTHONHASHSEED", "42")
