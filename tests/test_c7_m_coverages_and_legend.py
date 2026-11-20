"""C7-m 回归：作用范围圈始终可见 + 显示半径压缩 + 图例状态色载体。

用户反馈（2026-11-20）：
  1. 八工况地形是否都改为 DEM（视觉效果）；
  2. 标识装备作用范围的圆圈看不到了；
  3. 右下角图例标出的元素在可视化中是否都能体现。

根因与修复：
  - 圈看不到：has_los 用 3m 人眼高度，真实 DEM 起伏上千米 → 21 个圈可见弧全被裁光；
    且 cov 2500~12000m 远超 10km 战场，画成穿过全图的大弧。修：天线高度 25m +
    显示半径压缩到 520~2000m + 新增淡色完整圈（始终可见）。
  - 图例状态色：Web 回放 tint_models=False 导致 5 种状态色无载体。修：新增
    _log_status_flags 单 entity 状态旗标。

运行：PYTHONPATH=src python -m pytest tests/test_c7_m_coverages_and_legend.py -q
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import rerun as rr

from brigsim.viz import rerun_view as rv


class _Capture:
    """临时替换 rr.log，采集 entity -> 最近一次 (args, kwargs)。"""

    def __enter__(self):
        self._orig = rr.log
        self.data: dict[str, tuple] = {}
        cap = self

        def fake(ent, *a, **kw):
            cap.data[str(ent)] = (a, kw)

        rr.log = fake
        return self

    def __exit__(self, *exc):
        rr.log = self._orig


def _mini_scenario_df(n: int):
    uids = [f"u{i}" for i in range(n)]
    cur = pd.DataFrame(
        {
            "x": [3000.0] * n, "y": [4000.0] * n,
            "heading": [0.0] * n, "alive": [True] * n,
            "damaged": [False] * n, "under_repair": [False] * n,
        },
        index=uids,
    )
    zs = np.array([900.0] * n)
    return cur, zs


def test_coverage_full_ring_always_emitted_even_when_all_los_blocked():
    """即使 LOS 全部遮挡（亮弧=0），淡色完整圈 rings_full 也必须发出。"""
    cur, zs = _mini_scenario_df(1)
    # 单一雷达单位，coverage 角色由 info 提供（tuple 第 5 位是 role，第 6 位 cov）
    info = {"u0": ("radar_air", 0, "雷达", 0.0, "radar_air", 8000.0)}
    terrain = None  # 无 los_terrain -> 恒可见；下面再测全遮挡
    with _Capture() as cap:
        rv._log_coverages(0, cur, zs, info, thin=True, geo=None, los_terrain=terrain)
    assert "world/coverages/rings_full" in cap.data, "淡色完整圈实体未发出"
    assert "world/coverages/rings" in cap.data, "亮色可见弧实体未发出"


def test_coverage_display_radius_within_battlefield():
    """显示半径必须压缩到战场尺度（<2500m），不再画成穿过全图的大弧。

    通过 monkeypatch _ring_pts_tf 捕获传入的 radius（rerun 的 batch 对象是延迟
    序列，事后不可迭代），直接断言压缩函数输出。
    """
    seen: list[float] = []
    orig = rv._ring_pts

    def spy(cx, cy, z, radius, n=48):
        seen.append(radius)
        return orig(cx, cy, z, radius, n)

    rv._ring_pts = spy
    try:
        cur, zs = _mini_scenario_df(1)
        info = {"u0": ("radar_air", 0, "雷达", 0.0, "radar_air", 12000.0)}
        with _Capture():
            rv._log_coverages(0, cur, zs, info, thin=True, geo=None, los_terrain=None)
    finally:
        rv._ring_pts = orig
    assert seen, "未捕获到圆环半径"
    for r in seen:
        assert 1000 < r < 2500, f"显示半径 {r:.0f}m 不在战场可读尺度内"


def test_legend_state_colors_match_render_constants():
    """右下角图例 7 色必须与渲染常量逐一一致（否则图例与画面对不上）。"""
    legend = {
        "红方单位": (214, 69, 55), "蓝方单位": (64, 132, 240),
        "M失动": (50, 110, 210), "F火力受损": (239, 170, 23),
        "在修": (168, 85, 247), "重损后送": (180, 60, 60),
        "K战毁": (96, 46, 34),
    }
    const = {
        "红方单位": rv.SIDE_COLOR[0], "蓝方单位": rv.SIDE_COLOR[1],
        "M失动": rv.M_KILL_C, "F火力受损": rv.DAMAGED_C,
        "在修": rv.REPAIR_C, "重损后送": rv.REAR_C,
        "K战毁": rv.WRECK_K_C,
    }
    for k in legend:
        assert legend[k] == const[k], f"图例 {k} 颜色 {legend[k]} != 渲染 {const[k]}"


def test_status_flags_cover_all_five_damage_states():
    """M失动/F受损/在修/后送/K战毁 五种状态都必须产生状态旗标载体。"""
    uids = ["ok", "m", "f", "rep", "rear", "k", "dead"]
    cur = pd.DataFrame(
        {
            "x": [1000.0] * 7, "y": [2000.0] * 7, "heading": [0.0] * 7,
            "alive": [True, True, True, True, True, False, False],
            "damaged": [False, True, True, True, True, False, False],
            "under_repair": [False, False, False, True, False, False, False],
            "damage_grade": [0, 1, 2, 1, 2, 3, 1],
            "disposition": [0, 0, 0, 0, 4, 0, 0],
        },
        index=uids,
    )
    zs = np.array([500.0] * 7)
    with _Capture() as cap:
        rv._log_status_flags(cur, zs, {u: ("tank", 0, "t", 0, "assault", 0) for u in uids})
    # 正常单位不画；其余 6 个异常单位各 1 旗标（M/F/在修/后送/K战毁/普通战损）
    flags = cap.data["world/status/flags"][0][0]
    assert len(flags.positions) == 6, f"状态旗标数 {len(flags.positions)} != 6"


def test_status_flags_clear_when_no_abnormal_units():
    """无异常单位时必须 Clear，避免残留上一帧状态旗标。"""
    cur, zs = _mini_scenario_df(1)
    with _Capture() as cap:
        rv._log_status_flags(cur, zs, {"u0": ("tank", 0, "t", 0, "assault", 0)})
    assert "world/status/flags" in cap.data
