# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller runtime hook —— Linux Full 便携包启用 Paddle 共享库。

Paddle 的 C++ 动态加载器（phi/backends/dynload/dynamic_loader.cc）用裸名
dlopen（如 "libmklml_intel.so"）加载其第三方库，裸名 dlopen 只查
LD_LIBRARY_PATH / 系统缓存路径，**不**查 .so 自身的 RPATH/RUNPATH
（RPATH 仅作用于 .so 加载其依赖时，对显式 dlopen 裸名无效）。因此仅靠
patchelf --set-rpath '$ORIGIN' 修正库的 RPATH 不足以让 portable 产物内
import paddle 成功（v0.9.1 Linux CI 实测：RPATH 修正后仍在
dynamic_loader.cc:409 报 'libmklml_intel.so: cannot open shared object file'）。

本 hook 在 Python 启动早期（任何 import paddle 之前）把产物内
_pi_internal/paddle/libs 追加到 LD_LIBRARY_PATH（glibc 动态加载器在
dlopen 调用时读取当前进程环境），使裸名 dlopen 能找到 paddle/libs 下的
第三方库。同时保留 patchelf 的 RPATH 修正（覆盖 .so 依赖链解析）。

frozen 环境下 sys._MEIPASS == onedir 的 _internal 目录。
"""
import os
import sys

_LIBS_RELATIVE = os.path.join("paddle", "libs")
if getattr(sys, "frozen", False):
    libs_dir = os.path.join(sys._MEIPASS, _LIBS_RELATIVE)  # noqa: SLF001 - PyInstaller 私有常量
    if os.path.isdir(libs_dir):
        parts = [libs_dir]
        existing = os.environ.get("LD_LIBRARY_PATH", "")
        if existing:
            parts.append(existing)
        os.environ["LD_LIBRARY_PATH"] = os.pathsep.join(parts)