"""装备"四率"指标口径（比赛 C1，GJB 术语对齐，原型解释）。

口径说明（参照 GJB 可靠性/维修性术语系列的通用定义做的原型解释，
权威条文以正式标准为准；公式已同步进单测）：

- 完好率 A(t)：时刻 t "可用"装备数 / 编制（参战）总数。
  可用 = 存活 且 未受损（战斗受损/自然故障均未发生）且 不在修。
  汇总给出 终值完好率（metrics.readiness）与 时序平均完好率 availability_mean。
- 战损率 D(t)：累计战损（被击毁、不可修复）数 / 参战总数，
  即现有 attrition 指标；战斗受损（damaged，可修复）另计事件流不入战损。
- 故障率 λ：自然故障事件数 / 累计暴露时间（可用单位·tick，
  即逐 tick 可用数的累加）；另按任务 tick 时长换算为"每 100 单位·小时"。
- 修复率 R(t)：累计修复完成（归建）/ 累计应修。
  应修 = 进入抢修通道的事件数（战斗受损 + 自然故障）；战毁不可修不计应修，
  在修期间被战毁则计入应修、不计修复完成（修复率自然下降）。
任务完成率（success）为顶层效能指标，不属装备四率。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..kernel.state import SimState
from ..sustainment.repair import BLUE, RED, SustainmentModel

# 事件类型 -> 时序累计列名
_EVENT_COLS = {
    "failure_natural": "failures_natural_cum",
    "damage_combat": "damage_combat_cum",
    "repair_start": "repairs_required_cum",
    "repair_done": "repairs_done_cum",
}


def side_totals(state: SimState) -> dict[int, int]:
    """各方参战总数（初始编制）。"""
    return {side: int((state.side == side).sum()) for side in (RED, BLUE)}


def four_rates_summary(
    state: SimState,
    sustain: SustainmentModel,
    snap: pd.DataFrame | None = None,
) -> dict[str, float]:
    """单次运行的四率汇总（每方 red/blue 一组键）。

    键：failures_natural_* / failures_combat_*（事件计数）、
    repairs_required_* / repairs_done_* / repair_rate_*（修复率）、
    availability_mean_*（时序平均完好率，需快照表）、
    failure_rate_*（故障率，可用单位·tick 口径，需快照表）、
    failure_rate_100h_*（换算为每 100 单位·小时）。
    战损率即 metrics 的 attrition_*，完好率终值即 readiness_*，不在此重复。
    """
    out: dict[str, float] = {}
    totals = side_totals(state)
    dt = float(sustain.scenario.mission.dt_seconds)
    for side, name in ((RED, "red"), (BLUE, "blue")):
        out[f"failures_natural_{name}"] = float(sustain.failures_natural[side])
        out[f"failures_combat_{name}"] = float(sustain.failures_combat[side])
        req = sustain.repairs_required[side]
        done = sustain.repairs_done[side]
        out[f"repairs_required_{name}"] = float(req)
        out[f"repairs_done_{name}"] = float(done)
        out[f"repair_rate_{name}"] = done / req if req > 0 else 1.0
        if snap is not None and not snap.empty and totals[side] > 0:
            sub = snap[snap["side"] == side]
            avail = sub["alive"] & ~sub["damaged"] & ~sub["under_repair"]
            out[f"availability_mean_{name}"] = float(avail.mean())
            exposure = float(avail.sum())  # 可用单位·tick = 暴露时间
            rate = sustain.failures_natural[side] / exposure if exposure > 0 else 0.0
            out[f"failure_rate_{name}"] = rate
            out[f"failure_rate_100h_{name}"] = rate * (3600.0 / dt) * 100.0
    return out


def four_rates_timeseries(
    state: SimState, snap: pd.DataFrame, events: pd.DataFrame
) -> pd.DataFrame:
    """四率时序（每 tick 每方一行）。

    列：availability（完好率）、attrition（战损率）、
    failures_natural_cum / damage_combat_cum（累计自然故障/战斗受损事件）、
    repairs_required_cum / repairs_done_cum（累计应修/修复完成）、
    repair_rate（修复率，累计口径）、failure_rate（故障率，累计暴露口径）。
    事件按其 DES 时刻向上取整归入 tick（advance(until=t) 处理 ≤t 的事件）。
    """
    totals = side_totals(state)
    df = snap.assign(available=snap["alive"] & ~snap["damaged"] & ~snap["under_repair"])
    agg = (
        df.groupby(["tick", "side"], sort=True)
        .agg(available_n=("available", "sum"), alive_n=("alive", "sum"))
        .reset_index()
    )
    n_by_side = agg["side"].map(totals)
    agg["availability"] = agg["available_n"] / n_by_side
    agg["attrition"] = 1.0 - agg["alive_n"] / n_by_side

    for col in _EVENT_COLS.values():
        agg[col] = 0
    if events is not None and not events.empty:
        ev = events.assign(tick=np.ceil(events["tick"]).astype(int))
        cnt = ev.groupby(["side", "tick", "type"]).size()
        for (side, tick, type_), k in cnt.items():
            col = _EVENT_COLS.get(type_)
            if col is not None:
                agg.loc[(agg["side"] == side) & (agg["tick"] >= tick), col] += int(k)

    agg["repair_rate"] = np.where(
        agg["repairs_required_cum"] > 0,
        agg["repairs_done_cum"] / agg["repairs_required_cum"],
        1.0,
    )
    exposure_cum = agg.groupby("side")["available_n"].cumsum()
    agg["failure_rate"] = np.where(
        exposure_cum > 0, agg["failures_natural_cum"] / exposure_cum, 0.0
    )
    cols = [
        "tick",
        "side",
        "availability",
        "attrition",
        "failures_natural_cum",
        "damage_combat_cum",
        "repairs_required_cum",
        "repairs_done_cum",
        "repair_rate",
        "failure_rate",
    ]
    return agg[cols].sort_values(["side", "tick"]).reset_index(drop=True)
