"""M1 装备保障模型：SimPy 离散事件进程，按 tick 边界与交战内核同步。

机制（粗版，参数均为占位，后续轮次标定/扩展）：
- 每方一个 simpy.Resource 抢修组（容量 = repair_teams）；
- 战斗受损与自然故障共用同一条抢修通道：送修延迟 -> 备件请领 -> 排队 ->
  维修（指数 MTTR）-> 归建；
- M3 备件供应链：可选库存（spare_stock），每次维修消耗 1 件，
  缺货时维修挂起等待周期性补给（resupply_interval/qty）；备件在抢修组
  之前请领，等件不占用抢修组；
- C7-a1 分部件备件（part_stock 非空即激活）：每方每部件一个库存容器，
  按受损部件请领 spare_units 件，修复时长取自部件目录（repair_ticks），
  轻损现地处置减半、M 级先付拖救时延（tow_delay_ticks）；
- 自然故障按指数 MTBF 抽样（战斗损毁 killed 不可修，进程直接终止）；
- 唯一状态真相是 SimState 列存（damaged/under_repair/damage_grade/
  part_id/disposition），本模块只在 DES 回调里改列，
  不持有装备状态对象，保持 M4 向量化/GPU 后端的可迁移性。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import simpy

from ..combat.damage import (
    DEFAULT_DAMAGE_TABLE,
    DEFAULT_PART_CATALOG,
    DISP_DRIVE_BACK,
    DISP_ONSITE,
    DISP_TOW,
    GRADE_F,
    GRADE_M,
    _sample_repairable,
)
from ..data.schema import Scenario
from ..kernel.state import SimState

RED, BLUE = 0, 1
SIDE_NAMES = {RED: "red", BLUE: "blue"}


class SustainmentModel:
    """一次想定一次运行的保障进程集合（时间单位 = 内核 tick）。"""

    def __init__(self, scenario: Scenario, state: SimState, rng) -> None:
        self.scenario = scenario
        self.state = state
        self.rng = rng
        self.env = simpy.Environment()
        spec = scenario.sustainment
        self.repair_delay = spec.repair_delay_ticks
        self.tow_delay = spec.tow_delay_ticks  # C7-a1：M 级拖救时延
        # SimPy Resource 容量必须 >0；None 表示该方无抢修能力（送修后无限挂起）
        self.teams = {
            side: (simpy.Resource(self.env, capacity=n) if n > 0 else None)
            for side, n in ((RED, spec.repair_teams["red"]), (BLUE, spec.repair_teams["blue"]))
        }
        # C7-a1 模式开关：part_stock 非空 或 damage_model/part_catalog 显式给出
        self.part_mode = bool(spec.part_stock) or spec.damage_model is not None \
            or spec.part_catalog is not None
        self.part_catalog = (
            spec.part_catalog if spec.part_catalog is not None else DEFAULT_PART_CATALOG
        )
        self.damage_table = (
            spec.damage_model if spec.damage_model is not None else DEFAULT_DAMAGE_TABLE
        )
        # 备件库存：池模式 {side: Container}；部件模式 {side: {part: Container}}；
        # None = 无限备件（不启用供应链约束）
        self.parts: dict[int, simpy.Container] | dict[int, dict[str, simpy.Container]] | None
        self.parts = None
        # 备件保障统计（仅启用库存时计数）：请领总数、即时满足数、缺货累计等待
        self.req_total = {RED: 0, BLUE: 0}
        self.req_immediate = {RED: 0, BLUE: 0}
        self.stockout_wait = {RED: 0.0, BLUE: 0.0}
        if self.part_mode:
            self.parts = {
                side: {
                    pid: simpy.Container(
                        self.env,
                        init=int(spec.part_stock.get(name, {}).get(pid, 0)),
                        capacity=float("inf"),
                    )
                    for pid in self.part_catalog
                }
                for side, name in ((RED, "red"), (BLUE, "blue"))
            }
            for side in (RED, BLUE):
                self.env.process(self._resupply_loop(side))
        elif spec.spare_stock is not None:
            self.parts = {
                side: simpy.Container(
                    self.env, init=int(spec.spare_stock[name]), capacity=float("inf")
                )
                for side, name in ((RED, "red"), (BLUE, "blue"))
            }
            for side in (RED, BLUE):
                self.env.process(self._resupply_loop(side))
        # C1 四率事件流与计数器（GJB 口径的故障率/修复率数据底座）：
        # type ∈ failure_natural（自然故障）/ damage_combat（战斗受损）/
        #        repair_start（应修，入抢修通道）/ repair_done（修复归建）
        self.events: list[dict] = []
        self.failures_natural = {RED: 0, BLUE: 0}
        self.failures_combat = {RED: 0, BLUE: 0}
        self.repairs_required = {RED: 0, BLUE: 0}
        self.repairs_done = {RED: 0, BLUE: 0}
        # 逐 tick 累计的"可用单位·tick"暴露量（C3：无快照批量后端算故障率用，
        # 口径同 four_rates 时序快照的 available 累加）；由步进循环 tick 末调用
        # count_exposure() 维护，单环境 Mesa 路径不调用（其故障率直接由快照算）
        self.exposure = {RED: 0.0, BLUE: 0.0}
        # 本 tick 战斗受损待入队集合：交战段只登记、tick 末统一进通道，
        # 与 GPU 批量内核"设备侧下 tick 才见 under_repair"的时序对齐
        # （否则 Mesa 受损当 tick 立即免疫，Warp 当 tick 仍可被打，分布分叉）
        self._pending_combat: set[int] = set()
        for i in range(state.n):
            self.env.process(self._failure_loop(i))

    # ---- 对外接口 ----

    def advance(self, tick: int) -> None:
        """把 DES 时钟推进到指定 tick（含该时刻前的全部故障/修复事件）。"""
        self.env.run(until=tick)

    def combat_damage(self, i: int, grade: int = 0, part: str = "",
                      disposition: int = 0) -> None:
        """交战内核判定单位受损（未毁）时调用：仅登记，tick 末 commit 入通道。

        C7-a1 调用方附带等级/部件/处置（旧调用只传 i，保持零改动兼容）。
        """
        self.state.damaged[i] = True
        self.state.damage_grade[i] = int(grade)
        self.state.part_id[i] = str(part)
        self.state.disposition[i] = int(disposition)
        self._pending_combat.add(int(i))
        side = int(self.state.side[i])
        self.failures_combat[side] += 1
        self._record(i, side, "damage_combat")

    def commit_combat_damage(self) -> None:
        """tick 末把本 tick 战斗受损单位统一送入抢修通道。"""
        for i in self._pending_combat:
            self._start_repair(i)
        self._pending_combat.clear()

    def supply_stats(self) -> dict[str, float]:
        """备件保障终局统计：每方即时满足率（无请领记 1.0）与缺货累计等待 tick。"""
        out: dict[str, float] = {}
        for side, name in ((RED, "red"), (BLUE, "blue")):
            total = self.req_total[side]
            out[f"fill_rate_{name}"] = (
                self.req_immediate[side] / total if total > 0 else 1.0
            )
            out[f"stockout_wait_{name}"] = float(self.stockout_wait[side])
        return out

    def total_spares(self, side: int) -> int:
        """当前备件存量统一读取点（events.snapshot 使用）。

        池模式=聚合容器存量；部件模式=各部件容器存量求和。
        """
        if self.parts is None:
            return -1
        if self.part_mode:
            return int(sum(c.level for c in self.parts[side].values()))
        return int(self.parts[side].level)

    def event_frame(self) -> pd.DataFrame:
        """C1 四率事件流表：tick/unit/side/type + C7-a1 等级/部件/处置。"""
        return pd.DataFrame(
            self.events,
            columns=["tick", "unit", "side", "type", "grade", "part", "disposition"],
        )

    def count_exposure(self) -> None:
        """tick 末累计"可用单位·tick"暴露量（存活 且 未受损 且 不在修）。

        批量后端无逐单位快照，靠本计数给故障率提供分母；调用方须保证在
        tick 末全部状态（alive/damaged/under_repair）同步并完成入队后调用。
        """
        alive = np.asarray(self.state.alive).astype(bool)
        damaged = np.asarray(self.state.damaged).astype(bool)
        under = np.asarray(self.state.under_repair).astype(bool)
        sides = np.asarray(self.state.side)
        avail = alive & ~damaged & ~under
        for side in (RED, BLUE):
            self.exposure[side] += float((avail & (sides == side)).sum())

    def _record(self, i: int, side: int, type_: str) -> None:
        self.events.append(
            {
                "tick": float(self.env.now),
                "unit": int(i),
                "side": side,
                "type": type_,
                "grade": int(self.state.damage_grade[i]),
                "part": str(self.state.part_id[i]),
                "disposition": int(self.state.disposition[i]),
            }
        )

    # ---- SimPy 进程 ----

    def _failure_loop(self, i: int):
        """自然故障循环：指数 MTBF 间隔抽样，故障后送修，修复后继续计时。"""
        env, s = self.env, self.state
        while True:
            yield env.timeout(float(self.rng.exponential(s.mtbf[i])))
            if not s.alive[i]:
                return
            if s.damaged[i] or s.under_repair[i]:
                continue  # 已在抢修通道中（战斗受损等），修完后重新计时
            s.damaged[i] = True
            side = int(s.side[i])
            self.failures_natural[side] += 1
            self._set_failure_part(i)
            self._record(i, side, "failure_natural")
            self._start_repair(i)

    def _set_failure_part(self, i: int) -> None:
        """自然故障的部件级定位：C7-a1 模式按 1/mtbf 权重抽部件（M→拖救、
        F→自行后送，无轻/重分流）；池模式保持等级 0（旧口径）。
        """
        s = self.state
        if self.part_mode:
            part = _sample_repairable(self.rng, self.part_catalog)
            spec = self.part_catalog.get(part)
            if spec is None:
                part = "mobility"
                spec = self.part_catalog.get(part)
            s.part_id[i] = part
            if spec.grade == "m":
                s.damage_grade[i] = GRADE_M
                s.disposition[i] = DISP_TOW
            else:
                s.damage_grade[i] = GRADE_F
                s.disposition[i] = DISP_DRIVE_BACK
        else:
            s.damage_grade[i] = 0
            s.part_id[i] = ""
            s.disposition[i] = 0

    def _start_repair(self, i: int) -> None:
        s = self.state
        if not s.alive[i] or s.under_repair[i]:
            return
        s.under_repair[i] = True
        side = int(s.side[i])
        self.repairs_required[side] += 1
        self._record(i, side, "repair_start")
        self.env.process(self._repair_process(i))

    def _resupply_loop(self, side: int):
        """周期补给：每隔 interval tick 投送 qty 件备件。

        池模式向聚合容器投送；部件模式向每部件容器各投送 qty 件。
        """
        env = self.env
        spec = self.scenario.sustainment
        while True:
            yield env.timeout(float(spec.resupply_interval_ticks))
            if self.part_mode:
                for c in self.parts[side].values():
                    yield c.put(spec.resupply_qty)
            else:
                yield self.parts[side].put(spec.resupply_qty)

    def _repair_process(self, i: int):
        env, s = self.env, self.state
        side = int(s.side[i])
        part = str(s.part_id[i]) if self.part_mode else ""

        def _abort(return_part: bool, part_name: str) -> None:
            if return_part and self.parts is not None:
                if self.part_mode:
                    c = self.parts[side].get(part_name)
                    if c is not None:
                        c.put(self.part_catalog[part_name].spare_units)
                else:
                    self.parts[side].put(1)
            s.under_repair[i] = False

        if self.repair_delay > 0:
            yield env.timeout(float(self.repair_delay))
        if not s.alive[i]:
            _abort(False, part)
            return
        # 备件请领（在占用抢修组之前）：缺货则挂起等补给
        req_container = None
        req_amount = 1
        if self.parts is not None:
            if self.part_mode:
                req_container = self.parts[side].get(part)
                if req_container is not None:
                    req_amount = self.part_catalog[part].spare_units
            else:
                req_container = self.parts[side]
            if req_container is not None:
                self.req_total[side] += 1
                if req_container.level >= req_amount:
                    self.req_immediate[side] += 1
                t_req = env.now
                yield req_container.get(req_amount)
                self.stockout_wait[side] += env.now - t_req
                if not s.alive[i]:
                    _abort(True, part)
                    return
        # C7-a1：M 级（机动受损）拖救时延（a2 起拖救车实体化，现以时延抽象）
        if self.part_mode and int(s.damage_grade[i]) == GRADE_M and self.tow_delay > 0:
            yield env.timeout(float(self.tow_delay))
            if not s.alive[i]:
                _abort(True, part)
                return
        team = self.teams[side]
        if team is None:
            yield env.event()  # 无抢修组：永久排队，进程挂起
            return
        with team.request() as req:
            yield req
            if not s.alive[i]:  # 排队期间被战毁：返还备件
                _abort(self.parts is not None, part)
                return
            # 修复时长：部件模式按部件目录（轻损现地处置减半），池模式指数 MTTR
            if self.part_mode and part in self.part_catalog:
                duration = float(self.rng.exponential(self.part_catalog[part].repair_ticks))
                if int(s.disposition[i]) == DISP_ONSITE:
                    duration *= 0.5
            else:
                duration = float(self.rng.exponential(s.mttr[i]))
            yield env.timeout(duration)
        # 维修期间被战毁则不归建；备件已消耗不返还，标志位无论如何都要清
        if s.alive[i]:
            self.repairs_done[side] += 1
            self._record(i, side, "repair_done")
            s.damaged[i] = False
            s.damage_grade[i] = 0
            s.part_id[i] = ""
            s.disposition[i] = 0
        s.under_repair[i] = False