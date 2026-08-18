"""OmniCrawler GUI 子包入口。

支持通过 `python -m omnicrawler.gui` 启动 PyQt6 图形工作台。
"""

from omnicrawler.gui.main import main

if __name__ == "__main__":
    raise SystemExit(main())
