"""api 入口层的共用依赖：把「按 wid 找登记」这类每个路由文件都要写一遍的取数收在一处。

为什么要这个模块：`Path(WorkdirRegistry.get(wid).path)` 这一句在 api 层原本有 3 份逐字相同的
私有函数定义（routes_items / routes_runs / routes_strategies）加 15 处调用点内联展开——
同一件事写 18 遍，改一次登记解析就得扫全层。行为侧唯一要守的是「未登记抛
``WorkdirNotFoundError``，由 ``api/app.py`` 的全局处理器翻成 404 problem+json」，
本模块只做解析、不掺异常翻译，语义与各处内联写法逐字相同。
"""

from __future__ import annotations

from pathlib import Path

from ..workdir import WorkdirRegistry

__all__ = ["workdir_root"]


def workdir_root(wid: str) -> Path:
    """wid → 工作目录路径（未登记 404 由异常处理器翻译）。

    Args:
        wid: 工作目录登记 id。

    Returns:
        该登记项指向的目录路径。

    Raises:
        WorkdirNotFoundError: id 未登记（上层处理器翻成 404 problem+json）。
    """
    return Path(WorkdirRegistry.get(wid).path)
