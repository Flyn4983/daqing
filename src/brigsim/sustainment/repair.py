"""M1 装备保障模型：SimPy 离散事件进程，按 tick 边界与交战内核同步。

机制（粗版，参数均为占位，后续轮次标定/扩展）：
- 每方一个 simpy.Resource 抢修组（容量 = repair_teams）；
- 战斗受损与自然故障共用同一条抢修通道：送修延迟 -> 备件请领 -> 排队 ->
  维修（指数 MTTR）-> 归建；
- M3 备件供应链：可选库存（spare_stock），每次维修消耗 1 件，
  缺货时维修挂起等待周期性补给（resupply_interval/qty）；备件在抢修组
  之前请领，等件不占用抢修组；
- 自然故障按指数 MTBF 抽样（战斗损毁 killed 不可修，进程直接终止）；
- 唯一状态真相是 SimState 列存（damaged/under_repair），本模块只在 DES 回调里改列，
  不持有装备状态对象，保持 M4 向量化/GPU 后端的可迁移性。
"""

from __future__ import annotations

import simpy

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
        # SimPy Resource 容量必须 >0；None 表示该方无抢修能力（送修后无限挂起）
        self.teams = {
            side: (simpy.Resource(self.env, capacity=n) if n > 0 else None)
            for side, n in ((RED, spec.repair_teams["red"]), (BLUE, spec.repair_teams["blue"]))
        }
        # 备件库存：None = 无限备件（不启用供应链约束）
        self.parts: dict[int, simpy.Container] | None = None
        # 备件保障统计（仅启用库存时计数）：请领总数、即时满足数、缺货累计等待
        self.req_total = {RED: 0, BLUE: 0}
        self.req_immediate = {RED: 0, BLUE: 0}
        self.stockout_wait = {RED: 0.0, BLUE: 0.0}
        if spec.spare_stock is not None:
            self.parts = {
                side: simpy.Container(
                    self.env, init=int(spec.spare_stock[name]), capacity=float("inf")
                )
                for side, name in ((RED, "red"), (BLUE, "blue"))
            }
            for side in (RED, BLUE):
                self.env.process(self._resupply_loop(side))
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

    def combat_damage(self, i: int) -> None:
        """交战内核判定单位受损（未毁）时调用：仅登记，tick 末 commit 入通道。"""
        self.state.damaged[i] = True
        self._pending_combat.add(int(i))

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
            self._start_repair(i)

    def _start_repair(self, i: int) -> None:
        s = self.state
        if not s.alive[i] or s.under_repair[i]:
            return
        s.under_repair[i] = True
        self.env.process(self._repair_process(i))

    def _resupply_loop(self, side: int):
        """周期补给：每隔 interval tick 向该方库存投送 qty 件备件。"""
        env = self.env
        spec = self.scenario.sustainment
        while True:
            yield env.timeout(float(spec.resupply_interval_ticks))
            yield self.parts[side].put(spec.resupply_qty)

    def _repair_process(self, i: int):
        env, s = self.env, self.state
        side = int(s.side[i])

        def _abort(return_part: bool) -> None:
            if return_part:
                self.parts[side].put(1)
            s.under_repair[i] = False

        if self.repair_delay > 0:
            yield env.timeout(float(self.repair_delay))
        if not s.alive[i]:
            _abort(False)
            return
        # 备件请领（在占用抢修组之前）：缺货则挂起等补给
        parts = self.parts[side] if self.parts is not None else None
        if parts is not None:
            self.req_total[side] += 1
            if parts.level >= 1:
                self.req_immediate[side] += 1
            t_req = env.now
            yield parts.get(1)
            self.stockout_wait[side] += env.now - t_req
            if not s.alive[i]:
                _abort(True)
                return
        team = self.teams[side]
        if team is None:
            yield env.event()  # 无抢修组：永久排队，进程挂起
            return
        with team.request() as req:
            yield req
            if not s.alive[i]:  # 排队期间被战毁：返还备件
                _abort(parts is not None)
                return
            yield env.timeout(float(self.rng.exponential(s.mttr[i])))
        # 维修期间被战毁则不归建；备件已消耗不返还，标志位无论如何都要清
        if s.alive[i]:
            s.damaged[i] = False
        s.under_repair[i] = False
