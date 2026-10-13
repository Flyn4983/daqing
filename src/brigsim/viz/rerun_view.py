"""M1.5 可视化：rerun 3D 态势回放 + 指标时间曲线。

输入为实验层统一事件日志（snapshot / engagement DataFrame）：
- 每个单位一条 3D 轨迹，红/蓝着色，受损变黄，被击毁后停留在最后位置并置灰；
- 交火事件以箭头绘制（击毁=红，致损=黄，未毁=灰）；
- 任务目标点为绿色信标；
- 战损率/完好率随仿真时间记录为标量曲线。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import rerun as rr
import rerun.blueprint as rrb


def _make_blueprint() -> rrb.Blueprint:
    """默认布局：上方大 3D 态势（斜视相机），下方红/蓝指标标签页。"""
    scene = rrb.Spatial3DView(
        name="战场态势",
        origin="/world",
        background=(18, 20, 26),
        line_grid=rrb.LineGrid3D(spacing=1000.0, color=(70, 76, 90)),
        eye_controls=rrb.EyeControls3D(
            position=[3800.0, -4500.0, 7500.0],
            look_target=[5000.0, 5000.0, 0.0],
            eye_up=[0.0, 0.0, 1.0],
        ),
    )
    metrics = rrb.Tabs(
        rrb.TimeSeriesView(name="红方 战损/完好", origin="/metrics/red"),
        rrb.TimeSeriesView(name="蓝方 战损/完好", origin="/metrics/blue"),
    )
    return rrb.Blueprint(
        rrb.Vertical(scene, metrics, row_shares=[3.0, 1.0]),
        rrb.BlueprintPanel(expanded=False),
        rrb.SelectionPanel(expanded=False),
        rrb.TimePanel(expanded=True),
    )


RED = (232, 70, 58)
BLUE = (80, 150, 255)
DAMAGED = (239, 170, 23)
DEAD = (110, 110, 110)
OBJECTIVE = (47, 220, 120)
SHOT_MISS = (150, 150, 150)


def visualize(
    snapshots: pd.DataFrame,
    engagements: pd.DataFrame,
    objective: tuple[float, float],
    dt_seconds: float = 30.0,
    rrd_path: str | Path | None = None,
    spawn: bool = False,
    app_id: str = "brigsim",
) -> None:
    rr.init(app_id, spawn=spawn)
    if rrd_path is not None:
        rr.save(str(rrd_path))
    rr.send_blueprint(_make_blueprint())

    # 静态要素：目标点
    rr.log(
        "world/objective",
        rr.Points3D([[objective[0], objective[1], 0.0]], radii=200, colors=[OBJECTIVE]),
        static=True,
    )

    # 位置查找表：(tick, unit_id) -> (x, y, state)
    pos = {(int(t), uid): (float(x), float(y)) for t, uid, x, y in
           snapshots[["tick", "unit_id", "x", "y"]].itertuples(index=False)}
    unit_side = snapshots.drop_duplicates("unit_id").set_index("unit_id")["side"].to_dict()
    tracks: dict[str, list] = {uid: [] for uid in unit_side}

    for tick, grp in snapshots.groupby("tick", sort=True):
        tick = int(tick)
        rr.set_time("tick", sequence=tick)
        rr.set_time("sim_clock", duration=float(tick * dt_seconds))

        cur = grp.set_index("unit_id")
        for uid, row in cur.iterrows():
            p = (float(row["x"]), float(row["y"]), 0.0)
            tracks[uid].append(p)
            side = int(unit_side[uid])
            if not row["alive"]:
                color = DEAD
            elif row["damaged"]:
                color = DAMAGED
            else:
                color = RED if side == 0 else BLUE
            ent = f"world/units/{uid}"
            # 当前位置点
            rr.log(ent, rr.Points3D([p], radii=120, colors=[color]))
            # 历史轨迹拖尾
            if len(tracks[uid]) > 1:
                rr.log(
                    f"{ent}/track",
                    rr.LineStrips3D([tracks[uid]], colors=[color], radii=6),
                )

        _log_engagements_at(tick, engagements, pos)
        _log_metrics(tick, grp)


def _log_engagements_at(tick: int, engagements: pd.DataFrame, pos: dict) -> None:
    if engagements.empty:
        return
    ev = engagements[engagements["tick"] == tick]
    origins, vectors, colors = [], [], []
    for _, e in ev.iterrows():
        a = pos.get((tick, e["shooter"]))
        b = pos.get((tick, e["target"]))
        if a is None or b is None:
            continue
        origins.append([a[0], a[1], 0.0])
        vectors.append([b[0] - a[0], b[1] - a[1], 0.0])
        colors.append(RED if e["killed"] else (DAMAGED if e["damaged"] else SHOT_MISS))
    if origins:
        rr.log(
            "world/engagements",
            rr.Arrows3D(origins=origins, vectors=vectors, colors=colors, radii=20),
        )


def _log_metrics(tick: int, grp: pd.DataFrame) -> None:
    for side, name in ((0, "red"), (1, "blue")):
        m = grp["side"] == side
        if not m.any():
            continue
        alive = grp.loc[m, "alive"]
        ready = alive & (~grp.loc[m, "damaged"])
        rr.log(f"metrics/{name}/attrition", rr.Scalars(float(1 - alive.mean())))
        rr.log(f"metrics/{name}/readiness", rr.Scalars(float(ready.mean())))
