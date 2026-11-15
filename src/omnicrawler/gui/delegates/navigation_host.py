"""导航跳转接线（条目 6 第二步）。

为什么单独放一个模块
--------------------
``main.py`` 受 C-#19 ratchet 门禁约束（**只降不升**，见
``tools/check_main_window_size.py`` 与 ``docs/MAINWINDOW_REFACTOR.md``）：
往主类里加方法会被门禁判红——这条纪律是对的，它防止组合根继续膨胀。

所以导航相关的接线按既有 delegate 模式下沉到这里，主类只保留一个极薄的转发
入口（``_goto_nav``），并且**净行数不增加**：新增的实现全部在本模块。

为什么跳转不能再靠静态行号
--------------------------
导航项现在按**能力就绪状态**条件显示（未装 PDF 就不显示 PDF 工作台），
所以 ``NavIndex.X`` 这类静态行号在运行期是错的。身份必须用
``navigation.NavKey``，行号由运行期映射表查出。
"""
from __future__ import annotations

from typing import Any


def goto_nav(window: Any, key: str) -> bool:
    """按 key 跳转到对应导航行，返回是否成功。

    目标项被能力过滤掉时返回 ``False`` 而不是抛异常：
    调用方据此决定降级方式（例如提示"该功能需要先安装组件"），
    而不是让整个动作静默失败。
    """
    row_by_key = getattr(window, "_nav_row_by_key", None)
    if not row_by_key:
        return False
    row = row_by_key.get(key)
    if row is None:
        return False
    nav = getattr(window, "_nav", None)
    if nav is None:
        return False
    nav.setCurrentRow(row)
    return True


def on_nav_changed(window: Any, index: int, *, toggle_text: Any = None) -> None:
    """导航行变化后的页面切换与各页预加载（条目 6 第二步）。

    按 **key** 分派，不再用栈索引魔法数字——原来的 ``page == 1/3/5/7/10/11``
    是把栈索引当成了语义标识，一旦栈顺序变动就全错（历史「结果与复核错页」
    的同类问题）。

    ``toggle_text`` 是由调用方传入的 ``(workspace_label, editor_label)``：
    按钮文案属于 i18n 展示层，本模块不依赖 ``_()``。
    """
    from ..navigation import NavKey

    pages = getattr(window, "_nav_pages", {})
    page = pages.get(index)
    if page is None:
        return
    transition = getattr(window, "_page_transition", None)
    if transition is not None:
        transition.show(page)

    key = getattr(window, "_nav_key_by_row", {}).get(index)
    toggle = getattr(window, "_toggle_btn", None)
    if toggle is not None and toggle_text is not None:
        workspace_label, editor_label = toggle_text
        toggle.setText(workspace_label if key == NavKey.YAML_EDITOR else editor_label)

    if key == NavKey.YAML_EDITOR:
        window._yaml_editor.update_from_config(window._config)
    elif key == NavKey.RESULTS:
        window._auto_load_results()
    elif key == NavKey.EVIDENCE:
        pass  # 证据查看器数据由 record_selected_for_review 信号加载
    elif key == NavKey.PDF_WORKBENCH:
        # §A-27：从侧栏进来也要带上首页记录的需求，否则指引仍是死胡同。
        # hasattr 守卫：导航处理器可能在页面创建完成前被触发。
        workbench = getattr(window, "_pdf_workbench", None)
        if workbench is not None:
            workbench.set_pending_request(
                str(window._home.property("last_nl_request") or "")
            )
    elif key == NavKey.DEVELOPER:
        window._developer_inspector.refresh()
    elif key == NavKey.SCENE:
        window._scene_panel.refresh_scenes()  # S4：进入场景面板时刷新
    elif key == NavKey.LOGIN_SESSION:
        # U3：进入登录会话页才刷新列表 + 弹首次提醒（不做后台轮询）
        login = getattr(window, "_login_session_view", None)
        if login is not None:
            login.activate()
    elif key == NavKey.QML_SHOWCASE:
        # Q1：进入试点页才扫描本地市场条目（不做后台轮询）
        showcase = getattr(window, "_qml_showcase", None)
        if showcase is not None:
            showcase.refresh()
