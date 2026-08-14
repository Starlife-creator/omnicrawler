"""GUI 测试共享 fixtures（P0-6 / P1-13 修复）。

全局强制 QT_QPA_PLATFORM=offscreen：headless CI（无显示设备）下
import 期构造 QApplication 不再失败或挂起。setdefault 不覆盖测试
文件自身的显式设置，且 conftest 在测试模块导入前即生效。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
