# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller runtime hook —— Linux Full 便携包启用 Paddle 共享库。

背景：paddle 的 C++ 动态加载器（phi/backends/dynload/dynamic_loader.cc）用
**裸名** dlopen（如 "libmklml_intel.so"）加载其第三方库。裸名 dlopen 的库
搜索规则（glibc dl-load.c）依先后为：
  1. 已加载库列表（相同 SONAME 已存在则直接复用）；
  2. LD_LIBRARY_PATH（**仅进程启动时**由 ld.so 解析一次缓存于
     __rtld_env_path_list.dirs，运行中修改 os.environ 无效）；
  3. DT_RPATH / DT_RUNPATH（仅作用于主程序加载其依赖，裸 dlopen 不查）；
  4. /etc/ld.so.cache 与系统默认目录。

libmklml_intel.so 存在于 paddle wheel 的 paddle/libs/（v0.9.1 Linux CI
实测），PyInstaller 已把它 collect 到 _internal/paddle/libs/，但裸 dlopen
在产物环境（无 LD_LIBRARY_PATH 预置、RPATH 不参与）下找不到它。

修复：本 hook 在 Python 启动早期（任何 import paddle 之前）用绝对路径
ctypes.CDLL() **预加载** paddle/libs/ 下所有共享库。glibc 的 dlopen 对已
加载库会直接复用（搜索规则第 1 条），此后 paddle 的裸名 dlopen 即可命中。
按依赖顺序加载（先基础运行时库，再依赖它们的上层库）；单库失败容忍——
若某库已由其它机制加载或本环境无关紧要，不阻塞启动（frozen 环境早期
无失败处理上下文，也不可让 hook 抛异常）。

frozen 环境下 sys._MEIPASS == onedir 的 _internal 目录。
"""
import ctypes
import glob
import os
import sys

_LIBS_RELATIVE = os.path.join("paddle", "libs")


def _preload_paddle_libs() -> None:  # pragma: no cover - 仅 frozen Linux 生效
    libs_dir = os.path.join(sys._MEIPASS, _LIBS_RELATIVE)  # noqa: SLF001 - PyInstaller 私有常量
    if not os.path.isdir(libs_dir):
        return
    # 基础运行时库先加载（上层库 dlopen 时 glibc 才能解析其依赖）；
    # 其余按文件名排序，mklml/phi 等最后。失败容忍。
    _ordering = [
        "libiomp5*", "libgomp*", "libgfortran*", "libquadmath*",
        "libblas*", "liblapack*", "libtbb*.so*", "libdnnl*",
        "libcommon*", "libwarpctc*", "libwarprnnt*",
        "libopenvino*", "libmklml*", "libphi*",
    ]
    for pattern in _ordering:
        for path in sorted(glob.glob(os.path.join(libs_dir, pattern))):
            try:
                ctypes.CDLL(os.path.abspath(path))
            except OSError:
                pass  # 由其它机制加载或非必需，忽略


if getattr(sys, "frozen", False):
    _preload_paddle_libs()