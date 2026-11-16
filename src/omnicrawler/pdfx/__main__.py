"""``python -m omnicrawler.pdfx`` 的入口（条目 7）。

**为什么是惰性导入**

原先这里是模块顶层的 ``from ..cli import main``。那让整个宿主 CLI 进入
``pdfx`` 的依赖闭包——而 ``pdfx`` 将来要作为**可选组件**分发，组件里没有 CLI，
顶层 import 会直接 ``ImportError``。

改为函数内导入后：
* 从 pdfx 内部导入任何核心模块（config / database / parser / ocr …）都**不会**
  连带加载 CLI；
* ``python -m omnicrawler.pdfx`` 的**行为不变**——仍然转发到主 CLI
  （这是历史契约：``pdf`` 是主 CLI 的子命令）。

守卫：``tests/unit/tools/test_pdfx_no_cli_dependency.py``
"""

from __future__ import annotations


def main() -> None:
    """转发到主 CLI（惰性导入，避免把 CLI 绑进 pdfx 的依赖闭包）。"""
    from ..cli import main as cli_main

    cli_main()


if __name__ == "__main__":
    main()
