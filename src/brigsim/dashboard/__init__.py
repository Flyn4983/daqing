"""C6 指挥大屏数据构建包：把 C1–C5 仿真/实验产物预生成为前端 JSON。

入口：`brigsim dash-build`（见 cli.py）或 :func:`brigsim.dashboard.build.build_all`。
所有产物 schema 名统一为 ``brigsim-dash/<name>/1``，JSON 不允许 NaN。
"""

from .build import build_all

__all__ = ["build_all"]
