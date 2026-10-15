"""C7-a2 保障实体化 + 器材三档储备测试。

覆盖（对应 .trae/documents/c7-combined-arms-realtime.md §B a2 验收项）：
1. 三档请领分级：现地处置只取携行档，进站运行档优先携行兜底；
2. 缺货挂起等前送波次广播，波次运量按 carry_share 分两档；
3. DES 时钟不倒拨：deliver_parts 下一 tick 才生效；
4. 抢救车物理拖救链：认领→挂钩→拖回→卸车→修复归建；
5. 拖救途中 arv 被毁：受损车幸存重新排队，第二辆 arv 接续；
6. F 级自行后送进站检测；ONSITE 现地换件不移动；
7. 器材波次途中被击毁损失并重派；弹药车抵站补弹；
8. 蓝方（未声明 depot）零漂移走 a1 时间抽象；
9. Warp 守卫拒绝 support_echelon 想定；
10. 无 echelon 时 SupportOps 空操作；supply_stats 新键。
"""

from __future__ import annotations

import numpy as np
import pytest

from brigsim.combat.damage import (
    DISP_DRIVE_BACK,
    DISP_ONSITE,
    DISP_TOW,
    GRADE_F,
    GRADE_M,
)
from brigsim.combat.mesa_kernel import MesaKernel
from brigsim.data.schema import (
    Environment,
    PartSpec,
    PlatformTypeSpec,
    Scenario,
    SupportEchelonSpec,
    SustainmentSpec,
    Terrain,
    UnitSpec,
)
from brigsim.kernel.state import SimState
from brigsim.sustainment.repair import BLUE, RED, SustainmentModel

BIG = Terrain(source="synthetic", size_m=(10000.0, 10000.0), grid_n=20,
              base_height=0.0, amplitude=0.0, water_level=0.0, noise_seed=1)
STATION = [1500.0, 5000.0]
DEPOT = [200.0, 5000.0]

# 维修时长故意短，保证测试窗内完成物理链后能修复归建
_CATALOG = {
    "mobility": PartSpec(part_id="mobility", name="行走总成", grade="m",
                         mtbf_ticks=1e9, repair_ticks=2.0),
    "fire_control": PartSpec(part_id="fire_control", name="火控系统", grade="f",
                             mtbf_ticks=1e9, repair_ticks=2.0),
    "weapon": PartSpec(part_id="weapon", name="武器系统", grade="f",
                       mtbf_ticks=1e9, repair_ticks=2.0),
}


def _pt(role: str = "assault", **kw) -> PlatformTypeSpec:
    base = dict(
        sensor_range_m=1000.0, weapon_range_m=1000.0, pk=0.0,
        speed_m_per_tick=200.0, reload_ticks=1, detect_prob=1.0,
        track_ttl_ticks=5, role=role, mtbf_ticks=1e9, mttr_ticks=6.0,
    )
    base.update(kw)
    return PlatformTypeSpec(**base)


_TYPES = {
    "tank": _pt(role="assault"),
    "arv": _pt(role="support", weapon_range_m=0.0, speed_m_per_tick=200.0,
               sensor_range_m=50.0),
    "supply_veh": _pt(role="support", weapon_range_m=0.0, speed_m_per_tick=150.0,
                      sensor_range_m=50.0),
    "ammo_veh": _pt(role="support", weapon_range_m=0.0, speed_m_per_tick=150.0,
                    sensor_range_m=50.0),
    "howitzer": _pt(role="artillery", fire_mode="indirect", weapon_range_m=0.0,
                    speed_m_per_tick=0.0, sensor_range_m=50.0, ammo_basic_load=12),
}


def _u(uid: str, typ: str, x: float, y: float) -> UnitSpec:
    return UnitSpec(unit_id=uid, type=typ, pos=(x, y))


def _ech_spec(**ech_kw) -> SustainmentSpec:
    ech = dict(depot={"red": DEPOT}, wave_interval_ticks=10, wave_qty=2,
               carry_share=0.5,
               operating_stock={"red": {"mobility": 2, "fire_control": 2, "weapon": 0}},
               tow_speed_scale=0.5, ammo_wave_qty=6, ammo_dist_radius_m=3000.0)
    ech.update(ech_kw)
    return SustainmentSpec(
        repair_teams={"red": 3, "blue": 1},
        part_catalog=_CATALOG,
        part_stock={"red": {"mobility": 4, "fire_control": 2, "weapon": 2},
                    "blue": {"mobility": 2, "fire_control": 2, "weapon": 2}},
        tow_delay_ticks=4,
        repair_station={"red": STATION, "blue": [9500.0, 5100.0]},
        support_echelon=SupportEchelonSpec(**ech),
    )


def _scen(red, blue=None, sustain=None, duration: int = 160) -> Scenario:
    return Scenario(
        name="c7a2_test",
        mission={"duration_ticks": duration, "dt_seconds": 30,
                 "objective": [9999.0, 9999.0], "success_radius_m": 10},
        terrain=BIG,
        environment=Environment(visibility_factor=1.0),
        platform_types=_TYPES,
        sustainment=sustain if sustain is not None else _ech_spec(),
        forces={"red": red, "blue": blue or []},
    )


def _kernel(red, blue=None, sustain=None, duration: int = 160, seed: int = 0) -> MesaKernel:
    return MesaKernel(_scen(red, blue or [], sustain, duration), seed=seed)


def _unit_scen(sustain: SustainmentSpec, n: int = 2) -> Scenario:
    """直构 SimState/SustainmentModel 的最小无交战想定。"""
    return Scenario(
        name="a2_unit",
        mission={"duration_ticks": 200, "dt_seconds": 30,
                 "objective": [9999, 9999], "success_radius_m": 10},
        terrain=BIG,
        environment=Environment(visibility_factor=1.0),
        platform_types={"tank": _pt()},
        sustainment=sustain,
        forces={"red": [_u(f"r{i}", "tank", 300.0 + 100.0 * i, 5000.0)
                        for i in range(n)],
                "blue": []},
    )


# ----------------------------------------------------------------------
# 1. 三档请领分级（DES 直测）
# ----------------------------------------------------------------------

def test_ech_parts_have_two_tiers_blue_keeps_a1_shape():
    sc = _unit_scen(_ech_spec())
    state = SimState(sc)
    sus = SustainmentModel(sc, state, np.random.default_rng(0))
    assert sus.ech_sides == {RED}
    assert set(sus.parts[RED]) == {"carry", "operating"}
    # 蓝方未声明 depot：保持 a1 的 {部件: Container} 结构
    assert set(sus.parts[BLUE]) == {"mobility", "fire_control", "weapon"}
    assert sus.operating_spares(RED) == 4  # 2+2+0
    assert sus.operating_spares(BLUE) == -1


def test_onsite_repair_draws_carry_only():
    spec = _ech_spec()
    sc = _unit_scen(spec)
    state = SimState(sc)
    sus = SustainmentModel(sc, state, np.random.default_rng(1))
    sus.combat_damage(0, grade=GRADE_F, part="weapon", disposition=DISP_ONSITE)
    sus.commit_combat_damage()
    sus.advance(20)
    assert sus.repairs_done[RED] == 1
    assert int(sus.parts[RED]["carry"]["weapon"].level) == 1   # 2-1
    assert int(sus.parts[RED]["operating"]["weapon"].level) == 0
    assert not state.damaged[0]


def test_station_repair_draws_operating_first():
    spec = _ech_spec()  # carry fire_control=2 也有货；u1 用 mobility 区分
    sc = _unit_scen(spec)
    state = SimState(sc)
    sus = SustainmentModel(sc, state, np.random.default_rng(2))
    # 携行档 mobility 清零，仅运行档有 2 件
    sus.parts[RED]["carry"]["mobility"].get(4)
    sus.combat_damage(0, grade=GRADE_M, part="mobility", disposition=DISP_TOW)
    sus.commit_combat_damage()
    sus.advance(2)  # 停在待拖事件
    assert 0 in sus.pending_tow[RED]
    sus.complete_tow(0)
    sus.advance(30)
    assert sus.repairs_done[RED] == 1
    assert int(sus.parts[RED]["operating"]["mobility"].level) == 1
    assert int(sus.parts[RED]["carry"]["mobility"].level) == 0
    assert sus.tow_done[RED] == 1


def test_stockout_unblocks_on_wave_broadcast():
    # 两档 fire_control 均无货：进站后挂起等波次
    spec = _ech_spec(operating_stock={"red": {"mobility": 0, "fire_control": 0,
                                              "weapon": 0}})
    spec.part_stock["red"] = {"mobility": 0, "fire_control": 0, "weapon": 0}
    sc = _unit_scen(spec)
    state = SimState(sc)
    sus = SustainmentModel(sc, state, np.random.default_rng(3))
    sus.combat_damage(0, grade=GRADE_F, part="fire_control",
                      disposition=DISP_DRIVE_BACK)
    sus.commit_combat_damage()
    sus.advance(2)  # 修复进程先停到待进站事件
    assert 0 in sus.awaiting_station[RED]
    sus.complete_station(0)
    sus.advance(4)
    assert sus.repairs_done[RED] == 0
    assert sus.req_total[RED] == 1 and sus.req_immediate[RED] == 0
    # 波次送达在调用时刻不生效（SimPy until 事件 URGENT 优先，timeout(1)
    # 在再下一次 advance 时才触发），且 env 时钟不倒拨
    t0 = sus.env.now
    sus.deliver_parts(RED, {"fire_control": 2})
    assert sus.total_spares(RED) == 0 and sus.waves_completed[RED] == 0
    sus.advance(t0 + 1)
    assert sus.waves_completed[RED] == 0
    sus.advance(t0 + 2)
    assert sus.waves_completed[RED] == 1
    # carry_share=0.5：2 件平分携行/运行；等待中的进站请领随即取走运行档 1 件
    assert int(sus.parts[RED]["carry"]["fire_control"].level) == 1
    assert int(sus.parts[RED]["operating"]["fire_control"].level) == 0
    assert sus.total_spares(RED) == 1
    sus.advance(t0 + 25)
    assert sus.repairs_done[RED] == 1
    assert sus.stockout_wait[RED] > 0


# ----------------------------------------------------------------------
# 2. 抢救车物理拖救链（内核级）
# ----------------------------------------------------------------------

def test_arv_physical_tow_chain():
    k = _kernel([_u("t1", "tank", 3000.0, 5000.0),
                 _u("a1", "arv", 2000.0, 5000.0)], seed=10)
    s, sup = k.state, k.support
    assert sup.active and sup.arvs[RED] == [1]
    k.sustain.combat_damage(0, grade=GRADE_M, part="mobility", disposition=DISP_TOW)
    k.sustain.commit_combat_damage()
    hooked = towed_west = done = repaired = False
    for _ in range(90):
        k.step()
        if k.sustain.tow_hooked[RED] >= 1:
            hooked = True
        if 1 in sup.arv_towing and float(s.x[0]) < 2900.0:
            towed_west = True
        if k.sustain.tow_done[RED] >= 1 and \
                np.hypot(s.x[0] - STATION[0], s.y[0] - STATION[1]) < 400.0:
            done = True
        if not s.damaged[0] and s.alive[0]:
            repaired = True
            break
    assert hooked and towed_west and done and repaired
    assert k.sustain.tow_hooked[RED] == 1


def test_arv_killed_mid_tow_target_requeues():
    k = _kernel([_u("t1", "tank", 3000.0, 5000.0),
                 _u("a1", "arv", 2000.0, 5000.0),
                 _u("a2", "arv", 1200.0, 5000.0)], seed=11)
    s, sup = k.state, k.support
    k.sustain.combat_damage(0, grade=GRADE_M, part="mobility", disposition=DISP_TOW)
    k.sustain.commit_combat_damage()
    killed_first = False
    for _ in range(120):
        k.step()
        # 首车挂钩后即将其击毁，受损车应重新排队由第二辆接续
        if not killed_first and 1 in sup.arv_towing and s.alive[1]:
            s.alive[1] = False
            killed_first = True
        if not s.damaged[0] and s.alive[0]:
            break
    assert killed_first
    assert k.sustain.tow_hooked[RED] == 2          # 挂钩两次
    assert k.sustain.tow_done[RED] == 1            # 只有一次拖抵
    assert k.sustain.repairs_done[RED] == 1
    assert s.alive[0] and not s.damaged[0]


def test_f_unit_self_drives_then_station_completes():
    k = _kernel([_u("t1", "tank", 3000.0, 5000.0)], seed=12)
    s = k.state
    k.sustain.combat_damage(0, grade=GRADE_F, part="fire_control",
                            disposition=DISP_DRIVE_BACK)
    k.sustain.commit_combat_damage()
    for _ in range(80):
        k.step()
        if not s.damaged[0] and s.alive[0]:
            break
    assert np.hypot(s.x[0] - STATION[0], s.y[0] - STATION[1]) < 400.0
    assert not s.damaged[0] and s.alive[0]
    assert k.sustain.repairs_done[RED] == 1


def test_onsite_unit_holds_position_during_repair():
    # 武器维修时长拉长，保证观察窗内始终在修（修完归建后恢复机动是正确行为）
    slow = dict(_CATALOG)
    slow["weapon"] = PartSpec(part_id="weapon", name="武器系统", grade="f",
                              mtbf_ticks=1e9, repair_ticks=500.0)
    spec = _ech_spec()
    spec.part_catalog = slow
    k = _kernel([_u("t1", "tank", 4000.0, 5000.0)], sustain=spec, seed=13)
    s = k.state
    k.sustain.combat_damage(0, grade=GRADE_F, part="weapon",
                            disposition=DISP_ONSITE)
    k.sustain.commit_combat_damage()
    x0, y0 = float(s.x[0]), float(s.y[0])
    for _ in range(6):
        k.step()
        assert s.under_repair[0]
        assert float(s.x[0]) == pytest.approx(x0)
        assert float(s.y[0]) == pytest.approx(y0)


# ----------------------------------------------------------------------
# 3. 器材波次损失重派 + 弹药补弹
# ----------------------------------------------------------------------

def test_supply_wave_lost_then_redispatched():
    k = _kernel([_u("v1", "supply_veh", 200.0, 5000.0),
                 _u("v2", "supply_veh", 200.0, 4500.0)],
                sustain=_ech_spec(wave_interval_ticks=4), seed=14)
    s, sup = k.state, k.support
    killed = False
    for _ in range(70):
        k.step()
        if not killed and 0 in sup.sup_tasks and sup.sup_tasks[0][0] == "out" \
                and s.alive[0] and float(s.x[0]) < 900.0:
            s.alive[0] = False  # 前送途中击毁首车
            killed = True
        if k.sustain.waves_completed[RED] >= 1:
            break
    assert killed
    assert k.sustain.waves_lost[RED] == 1
    assert k.sustain.waves_completed[RED] == 1
    # 3 部件 × 2 件 = 6 件入两档
    assert k.sustain.total_spares(RED) >= 6
    kinds = {r["kind"] for r in k.log.effect_rows}
    assert {"supply_lost", "supply_wave"} <= kinds


def test_ammo_veh_refills_artillery_in_radius():
    k = _kernel([_u("amo", "ammo_veh", 200.0, 5000.0),
                 _u("hz1", "howitzer", 1500.0, 5200.0),   # 距站 200m
                 _u("hz2", "howitzer", 8000.0, 8000.0)],  # 远超分发半径
                sustain=_ech_spec(wave_interval_ticks=4), seed=15)
    s = k.state
    # 弹药基数耗尽
    s.ammo[1] = 0
    s.ammo[2] = 0
    for _ in range(60):
        k.step()
        if k.sustain.ammo_delivered[RED] > 0:
            break
    assert int(s.ammo[1]) == 6          # 每波补 6 发（未超基数 12）
    assert int(s.ammo[2]) == 0          # 半径外不补
    assert k.sustain.ammo_delivered[RED] == 6
    assert any(r["kind"] == "ammo_deliver" for r in k.log.effect_rows)


# ----------------------------------------------------------------------
# 4. 蓝方零漂移 / Warp 守卫 / 默认空操作 / 统计键
# ----------------------------------------------------------------------

def test_blue_side_keeps_a1_time_abstraction():
    """蓝方未声明 depot：M 级走 tow_delay 抽象，永不进物理待拖队列。"""
    sc = _scen([_u("rt", "tank", 1000.0, 5000.0)],
               [_u("bt", "tank", 9000.0, 5000.0)], duration=80)
    k = MesaKernel(sc, seed=16)
    bi = 1
    k.sustain.combat_damage(bi, grade=GRADE_M, part="mobility",
                            disposition=DISP_TOW)
    k.sustain.commit_combat_damage()
    for _ in range(40):
        k.step()
        if k.sustain.repairs_done[BLUE] >= 1:
            break
    assert k.sustain.repairs_done[BLUE] == 1
    assert k.sustain.tow_hooked[BLUE] == 0 and k.sustain.tow_done[BLUE] == 0
    stats = k.sustain.supply_stats()
    for key in ("waves_completed_blue", "waves_lost_blue", "tow_hooked_blue",
                "tow_done_blue", "ammo_delivered_blue"):
        assert stats[key] == 0.0


def test_warp_guard_rejects_echelon():
    from brigsim.combat.warp_kernel import WarpKernel

    with pytest.raises(NotImplementedError):
        WarpKernel(_scen([_u("rt", "tank", 1000.0, 5000.0)]), seed=1)


def test_support_ops_noop_without_echelon():
    spec = SustainmentSpec(
        part_catalog=_CATALOG,
        part_stock={"red": {"mobility": 2, "fire_control": 2, "weapon": 2},
                    "blue": {"mobility": 2, "fire_control": 2, "weapon": 2}},
        tow_delay_ticks=0,
        repair_station={"red": STATION, "blue": [9500.0, 5100.0]},
    )
    k = MesaKernel(_scen([_u("t1", "tank", 3000.0, 5000.0),
                          _u("a1", "arv", 2000.0, 5000.0)], sustain=spec), seed=17)
    assert not k.support.active
    # M 级按 a1 抽象延时修复，arv 不产生位移
    x0 = float(k.state.x[1])
    k.sustain.combat_damage(0, grade=GRADE_M, part="mobility",
                            disposition=DISP_TOW)
    k.sustain.commit_combat_damage()
    for _ in range(30):
        k.step()
        if not k.state.damaged[0]:
            break
    assert float(k.state.x[1]) == pytest.approx(x0)
    assert k.sustain.repairs_done[RED] == 1


def test_supply_stats_carries_a2_keys():
    sc = _unit_scen(_ech_spec())
    state = SimState(sc)
    sus = SustainmentModel(sc, state, np.random.default_rng(4))
    stats = sus.supply_stats()
    for side in ("red", "blue"):
        for key in ("waves_completed", "waves_lost", "tow_hooked",
                    "tow_done", "ammo_delivered"):
            assert f"{key}_{side}" in stats


def test_dug_in_defender_holds_position():
    """v4：掘壕防御单位发现射界外目标也固守阵位，不出阵前出接敌。

    a0 想定里蓝方掘壕单位会弃阵向西机动约 2km，三道阵地压缩成一道厚墙，
    进攻方 36 分钟内无建制突破可能（粗标定诊断证据）。
    """
    types = dict(_TYPES)
    types["dug_tank"] = _pt(sensor_range_m=2500.0)  # 看得见但射程仅 1000
    red = [_u("r1", "tank", 0.0, 5000.0)]
    blue = [UnitSpec(unit_id="b1", type="dug_tank",
                     pos=(3000.0, 5000.0), dug_in=True)]
    sc = Scenario(
        name="c7a2_dug_hold",
        mission={"duration_ticks": 30, "dt_seconds": 30,
                 "objective": [5000.0, 5000.0], "success_radius_m": 10},
        terrain=BIG,
        environment=Environment(visibility_factor=1.0),
        platform_types=types,
        sustainment=_ech_spec(),
        forces={"red": red, "blue": blue},
    )
    k = MesaKernel(sc, seed=7)
    s = k.state
    bi = list(s.unit_ids).index("b1")
    x0, y0 = 3000.0, 5000.0
    for _ in range(14):  # 红方抵 ~2800m，蓝方早于射界外发现目标
        k.step()
        assert float(s.x[bi]) == x0
        assert float(s.y[bi]) == y0
