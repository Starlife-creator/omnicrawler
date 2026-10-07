"""Visual regression test framework for OmniCrawler GUI components.

Captures screenshots of key widgets across all three themes,
compares against stored baselines, and flags pixel-level differences.

Prerequisites::

    pip install omnicrawler-platform[gui] Pillow

CI 与本地正式验收优先使用固定参考源码门禁（无需预装基线）：

    python tools/check_visual_regression.py --output <新的证据目录>

门禁从 constraints/visual-ref.txt 读取已审查提交，在相同环境生成参考，再比较
当前界面；CI 的 GUI 作业强制执行并上传证据。OMNI_BASELINE_DIR 可指定独立
参考目录；不要将来源或渲染环境未知的旧基线作为最终验收依据。

★ **基线不随源码包分发**（`baselines/` 不在版本控制里，因为像素结果与平台字体/
渲染器绑定，入仓会让 CI 变成随机红）。因此**新检出会整块 skip** 本模块 ——
这不是"通过"，是"没跑"。要做视觉回归请先在本机生成基线：

    OMNI_BASELINE=1 pytest tests/gui/visual/ -q      # 生成/更新基线
    pytest tests/gui/visual/ -q                      # 之后即可做像素对比

★ **如实声明基线的能力边界**：在无字形的离屏环境（本仓实测：连 ASCII 都渲染成
空心方框）里，基线承载的是**几何/布局**信息（谁比谁大、间距、圆角、渐变、阴影范围），
**不是字形信息**。所以"像素 0.00% 一致"能证明"布局没变"，不能证明"字没排错"。
要判字形请在有字体渲染的桌面会话里看图。

Directory structure::

    tests/gui/
    ├── visual/
    │   ├── __init__.py
    │   ├── conftest.py          # fixture: qapp with theme switching
    │   ├── test_snapshots.py    # actual snapshot tests
    │   └── baselines/           # 本机生成的参考图（不入仓）
    │       ├── light/
    │       ├── dark/
    │       └── high_contrast/
"""

from __future__ import annotations

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASELINE_DIR = Path(os.environ.get("OMNI_BASELINE_DIR", str(HERE / "baselines")))
TOLERANCE = 0.01  # 1% pixel difference threshold
