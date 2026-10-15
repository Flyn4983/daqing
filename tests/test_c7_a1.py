"""C7-a1 战损分级测试：M/F/K 分级、分部件备件、处置分流、带病参战。

覆盖（对应 .trae/documents/c7-combined-arms-realtime.md §B 验收项）：
1. 分级分布大样本（assault + 穿甲弹：K≈0.60、非 K 中 M≈0.65）；
2. 地雷权重专打行走部分；3. 修复归建后等级/部件/处置三列清零；
4. M 级战损失动原地待拖救；5. F 级自行后送驶向修理所；
6. 部件缺货挂起→周期补给恢复；7. 补给按部件容器逐件投送；
8. 初始带病 MTBF 折减（直构 + example_brigade 模板 worn_count）；
9. 四率 GJB 口径在 a1 模式下回归；10. 旧模式 roll 逻辑零漂移；
11. Warp 内核对 a1 要素守卫；12. snapshot 新列；
13. K 级交战日志 grade="k"；14. 轻损害地/重度后送占比；
15. M 修复时长均值 > F（部件目录 repair_ticks 生效）。
"""

from __future__ import annotations

import numpy as np
import pytest

from brigsim.combat.damage import (
    DISP_DRIVE_BACK,
    DISP_ONSITE,
    DISP_REAR,
    DISP_TOW,
    DISP_WRITEOFF,
    GRADE_F,
    GRADE_K,
    GRADE_M,
    grade_of,
    sample_part,
)
from brigsim.combat.mesa_kernel import MesaKernel
from brigsim.data.provider import YamlFileProvider
from brigsim.data.schema import (
    Environment,
    PartSpec,
    PlatformTypeSpec,
    Scenario,
    SustainmentSpec,
    Terrain,
    UnitSpec,
)
from brigsim.experiment.four_rates import four_rates_summary
from brigsim.kernel.state import SimState
from brigsim.sustainment.repair import RED, SustainmentModel

FLAT = Terrain(source="synthetic", size_m=(2000.0, 2000.0), grid_n=20,
               base_height=0.0, amplitude=0.0, water_level=0.0, noise_seed=1)
BIG = Terrain(source="synthetic", size_m=(10000.0, 10000.0), grid_n=20,
              base_height=0.0, amplitude=0.0, water_level=0.0, noise_seed=1)


def _pt(role: str = "assault", **kw) -> PlatformTypeSpec:
    """生成测试用平台类型；缺省 mtbf 极大以抑制自然故障干扰。"""
    base = dict(
        sensor_range_m=1000.0, weapon_range_m=1000.0, pk=1.0,
        speed_m_per_tick=100.0, reload_ticks=1, detect_prob=1.0,
        track_ttl_ticks=5, role=role, mtbf_ticks=1e9, mttr_ticks=6.0,
    )
    base.update(kw)
    return PlatformTypeSpec(**base)


def _u(uid: str, typ: str, x: float, y: float, **kw) -> UnitSpec:
    return UnitSpec(unit_id=uid, type=typ, pos=(x, y), **kw)


def _scen(red, blue, types, sustain=None, duration: int = 60, terrain=FLAT) -> Scenario:
    return Scenario(
        name="c7a1_test",
        mission={"duration_ticks": duration, "dt_seconds": 30,
                 "objective": [9999.0, 9999.0], "success_radius_m": 10},
        terrain=terrain,
        environment=Environment(visibility_factor=1.0),
        platform_types=types,
        sustainment=sustain if sustain is not None else SustainmentSpec(),
        forces={"red": red, "blue": blue},
    )


def _unit_scen(sustain: SustainmentSpec, n: int = 1) -> Scenario:
    """直接构造 SimState/SustainmentModel 的最小保障想定（无交战，mtbf 极大）。"""
    return Scenario(
        name="a1_unit",
        mission={"duration_ticks": 200, "dt_seconds": 30,
                 "objective": [9999, 9999], "success_radius_m": 10},
        terrain=Terrain(grid_n=9, amplitude=0.0, base_height=20.0, water_level=0.0),
        environment=Environment(visibility_factor=1.0),
        platform_types={"tank": PlatformTypeSpec(
            sensor_range_m=10.0, weapon_range_m=10.0, pk=0.0,
            speed_m_per_tick=0.0, mtbf_ticks=1e9, mttr_ticks=6.0)},
        sustainment=sustain,
        forces={"red": [UnitSpec(unit_id=f"r{i}", type="tank",
                                 pos=(100.0 + 100.0 * i, 100.0)) for i in range(n)],
                "blue": []},
    )


class _Seq:
    """固定序列随机源：resolve_hit 仅调用 random()，按给定序列返回。"""

    def __init__(self, vals):
        self.vals = list(vals)
        self.i = 0

    def random(self) -> float:
        v = self.vals[self.i % len(self.vals)]
        self.i += 1
        return v


# ----------------------------------------------------------------------
# 1. 分级分布（S12 大样本）
# ----------------------------------------------------------------------

def test_grade_distribution_matches_fatal_weights():
    """assault + 穿甲弹：致命（车体/弹药架）占比 0.60；非致命中 M 占 0.65。"""
    rng = np.random.default_rng(0)
    n = 20000
    parts = [sample_part("assault", "ap", rng) for _ in range(n)]
    k = sum(1 for p in parts if grade_of(p) == "k")
    non_k = [p for p in parts if grade_of(p) != "k"]
    m = sum(1 for p in non_k if grade_of(p) == "m")
    assert abs(k / n - 0.60) <= 0.05
    assert abs(m / len(non_k) - 0.65) <= 0.05


def test_mine_weights_hit_mobility_only():
    """地雷权重整组覆盖：只落行走/动力总成（+致命项），绝不落火控/武器。"""
    rng = np.random.default_rng(1)
    n = 5000
    parts = [sample_part("assault", "mine", rng) for _ in range(n)]
    assert all(p not in ("fire_control", "weapon") for p in parts)
    moving = sum(1 for p in parts if p in ("mobility", "powerpack"))
    assert moving / n > 0.7


# ----------------------------------------------------------------------
# 2. 修复归建后等级/部件/处置三列清零
# ----------------------------------------------------------------------

def test_repair_done_clears_grade_columns():
    sus_spec = SustainmentSpec(part_stock={"red": {"mobility": 5}, "blue": {}},
                               tow_delay_ticks=0)
    sc = _unit_scen(sus_spec)
    state = SimState(sc)
    sus = SustainmentModel(sc, state, np.random.default_rng(2))
    sus.combat_damage(0, grade=GRADE_M, part="mobility", disposition=DISP_ONSITE)
    sus.commit_combat_damage()
    assert int(state.damage_grade[0]) == GRADE_M
    assert str(state.part_id[0]) == "mobility"
    assert int(state.disposition[0]) == DISP_ONSITE
    sus.advance(80)
    assert sus.repairs_done[RED] == 1
    assert not state.damaged[0] and not state.under_repair[0]
    assert int(state.damage_grade[0]) == 0
    assert str(state.part_id[0]) == ""
    assert int(state.disposition[0]) == 0


# ----------------------------------------------------------------------
# 3. 机动：M 失动原地待救 / F 自行后送
# ----------------------------------------------------------------------

_SLOW = {
    "mobility": PartSpec(part_id="mobility", name="行走总成", grade="m",
                         mtbf_ticks=1e9, repair_ticks=500.0),
    "fire_control": PartSpec(part_id="fire_control", name="火控系统", grade="f",
                             mtbf_ticks=1e9, repair_ticks=500.0),
}


def _move_sustain() -> SustainmentSpec:
    """慢修部件目录（测试窗内修不完）+ 两侧修理所（红在西侧 x=500）。"""
    return SustainmentSpec(
        part_catalog=_SLOW,
        part_stock={"red": {"mobility": 5, "fire_control": 5}, "blue": {}},
        tow_delay_ticks=0,
        repair_station={"red": [500.0, 5000.0], "blue": [9500.0, 5000.0]},
    )


def test_m_grade_unit_holds_position():
    """M 级（行走/动力总成）：虽已在修，仍原地不动待拖救（a2 拖救车实体化）。"""
    sc = _scen([_u("r1", "tank", 1000.0, 5000.0)], [], {"tank": _pt()},
               sustain=_move_sustain(), terrain=BIG)
    k = MesaKernel(sc, seed=3)
    s = k.state
    k.sustain.combat_damage(0, grade=GRADE_M, part="mobility", disposition=DISP_TOW)
    k.sustain.commit_combat_damage()
    assert s.under_repair[0]
    x0, y0 = float(s.x[0]), float(s.y[0])
    for _ in range(5):
        k.step()
    assert float(s.x[0]) == pytest.approx(x0)
    assert float(s.y[0]) == pytest.approx(y0)
    assert int(s.damage_grade[0]) == GRADE_M


def test_f_grade_unit_drives_back_to_station():
    """F 级（火控/武器）：自行后送，受损机动折减 ×0.5 速度驶向本方修理所。"""
    sc = _scen([_u("r1", "tank", 1000.0, 5000.0)], [], {"tank": _pt()},
               sustain=_move_sustain(), terrain=BIG)
    k = MesaKernel(sc, seed=4)
    s = k.state
    k.sustain.combat_damage(0, grade=GRADE_F, part="fire_control",
                            disposition=DISP_DRIVE_BACK)
    k.sustain.commit_combat_damage()
    x0 = float(s.x[0])
    for _ in range(3):
        k.step()
    assert float(s.x[0]) == pytest.approx(x0 - 150.0, abs=2.0)  # 3 tick × 100 × 0.5
    assert int(s.damage_grade[0]) == GRADE_F


# ----------------------------------------------------------------------
# 4. 分部件备件：缺货挂起恢复 / 按部件容器补给
# ----------------------------------------------------------------------

def test_stockout_waits_for_resupply_then_recovers():
    catalog = {"weapon": PartSpec(part_id="weapon", name="武器系统", grade="f",
                                  mtbf_ticks=1e9, repair_ticks=1.0)}
    sus_spec = SustainmentSpec(
        part_catalog=catalog, part_stock={"red": {"weapon": 1}, "blue": {}},
        resupply_interval_ticks=5, resupply_qty=1, tow_delay_ticks=0,
    )
    sc = _unit_scen(sus_spec, n=2)
    state = SimState(sc)
    sus = SustainmentModel(sc, state, np.random.default_rng(7))
    sus.combat_damage(0, grade=GRADE_F, part="weapon", disposition=DISP_ONSITE)
    sus.commit_combat_damage()
    sus.advance(2)
    sus.combat_damage(1, grade=GRADE_F, part="weapon", disposition=DISP_ONSITE)
    sus.commit_combat_damage()
    assert state.under_repair[1]
    sus.advance(3)  # t≈5 前备件为零：unit1 挂起等补给
    assert state.under_repair[1] and sus.repairs_done[RED] <= 1
    sus.advance(60)
    assert sus.repairs_done[RED] == 2
    assert int(state.damage_grade[1]) == 0
    assert str(state.part_id[1]) == ""
    assert int(state.disposition[1]) == 0
    assert sus.stockout_wait[RED] > 0
    assert sus.req_total[RED] == 2 and sus.req_immediate[RED] == 1


def test_resupply_fills_each_part_container():
    catalog = {
        "mobility": PartSpec(part_id="mobility", grade="m",
                             mtbf_ticks=1e9, repair_ticks=4.0),
        "powerpack": PartSpec(part_id="powerpack", grade="m",
                              mtbf_ticks=1e9, repair_ticks=5.0),
    }
    sus_spec = SustainmentSpec(part_catalog=catalog, part_stock={"red": {}, "blue": {}},
                               resupply_interval_ticks=5, resupply_qty=3)
    sc = _unit_scen(sus_spec)
    state = SimState(sc)
    sus = SustainmentModel(sc, state, np.random.default_rng(11))
    assert sus.part_mode and sus.total_spares(RED) == 0
    sus.advance(6)
    assert sus.total_spares(RED) == 6  # 2 部件容器 × 每件 3


# ----------------------------------------------------------------------
# 5. 初始带病装备 MTBF 折减
# ----------------------------------------------------------------------

def test_worn_mtbf_scaling_in_state():
    sus_spec = SustainmentSpec(worn_mtbf_scale=0.6)
    sc = _scen(
        [_u("r0", "tank", 100.0, 100.0),
         _u("r1", "tank", 200.0, 100.0, initial_worn=True)],
        [], {"tank": _pt(mtbf_ticks=500.0)}, sustain=sus_spec,
    )
    s = SimState(sc)
    assert float(s.mtbf[0]) == pytest.approx(500.0)
    assert float(s.mtbf[1]) == pytest.approx(300.0)


def test_example_brigade_worn_templates_scaled():
    sc = YamlFileProvider().load_scenario("example_brigade")
    sus = sc.sustainment
    assert sus.worn_mtbf_scale == pytest.approx(0.6)
    s = SimState(sc)
    n_red = len(sc.forces["red"])
    n_worn = 0
    for side_name, offset in (("red", 0), ("blue", n_red)):
        for j, u in enumerate(sc.forces[side_name]):
            base = sc.platform_types[u.type].mtbf_ticks
            if u.initial_worn:
                n_worn += 1
                assert float(s.mtbf[offset + j]) == pytest.approx(
                    base * sus.worn_mtbf_scale)
            else:
                assert float(s.mtbf[offset + j]) == pytest.approx(base)
    assert n_worn == 3  # 红 r1t 坦克连 worn_count=2 + 蓝 bt 坦克排 worn_count=1


# ----------------------------------------------------------------------
# 6. 四率口径回归 + 旧模式零漂移
# ----------------------------------------------------------------------

def test_four_rates_regression_in_a1_mode():
    """a1 想定跑通后，C1 四率汇总键全集与值域口径不变（硬回归锚）。"""
    types = {"tk": _pt(pk=0.3)}
    sus_spec = SustainmentSpec(
        part_stock={"red": {"mobility": 3}, "blue": {"mobility": 3}},
        tow_delay_ticks=0)
    sc = _scen([_u("r1", "tk", 100.0, 100.0)], [_u("b1", "tk", 300.0, 100.0)],
               types, sustain=sus_spec, duration=60)
    k = MesaKernel(sc, seed=12)
    while not k.finished:
        k.step()
    snap, _ = k.log.to_frames()
    summ = four_rates_summary(k.state, k.sustain, snap)
    for side in ("red", "blue"):
        assert 0.0 <= summ[f"repair_rate_{side}"] <= 1.0
        assert summ[f"repairs_done_{side}"] <= summ[f"repairs_required_{side}"]
    for key in ("failures_natural_red", "failures_combat_red", "repairs_required_red",
                "repairs_done_red", "availability_mean_red", "failure_rate_red",
                "failure_rate_100h_red", "failures_natural_blue", "failures_combat_blue",
                "repairs_required_blue", "repairs_done_blue", "repair_rate_blue"):
        assert key in summ


def test_legacy_mode_matches_old_roll_logic():
    """未启用 a1（无 part_stock/damage_model/part_catalog）时逐字节等价旧逻辑。"""
    sc = _unit_scen(SustainmentSpec(), n=3)
    k = MesaKernel(sc, seed=13)
    assert not k.damage_model.enabled
    k.damage_model.rng = _Seq([0.10, 0.40, 0.90])
    before = len(k.log.engagements)
    killed_t, hit_t = k.damage_model.resolve_hit("shooter", 0, 0.3, "ap", "direct")
    killed_d, hit_d = k.damage_model.resolve_hit("shooter", 1, 0.3, "ap", "direct")
    killed_m, hit_m = k.damage_model.resolve_hit("shooter", 2, 0.3, "ap", "direct")
    s = k.state
    assert killed_t is True and hit_t is True and not s.alive[0] and not s.damaged[0]
    assert killed_d is False and hit_d is True and s.alive[1] and s.damaged[1]
    assert killed_m is False and hit_m is False and s.alive[2] and not s.damaged[2]
    assert bool((s.damage_grade == 0).all())
    assert bool((s.part_id == "").all())
    assert bool((s.disposition == 0).all())
    assert len(k.log.engagements) - before == 3  # 脱靶也记日志（旧行为）
    assert all(row["grade"] == "" for row in k.log.engagements[-3:])


# ----------------------------------------------------------------------
# 7. Warp 守卫 + snapshot 新列
# ----------------------------------------------------------------------

def test_warp_guard_rejects_a1_scenarios():
    from brigsim.combat.warp_kernel import WarpKernel

    with pytest.raises(NotImplementedError):
        WarpKernel(_unit_scen(SustainmentSpec(part_stock={"red": {"mobility": 1}})),
                   seed=1)
    k = WarpKernel(_unit_scen(SustainmentSpec()), seed=1)  # 干净旧想定可构造
    assert k.name == "warp"


def test_snapshot_records_grade_columns():
    sus_spec = SustainmentSpec(
        part_stock={"red": {"mobility": 5}, "blue": {"mobility": 5}},
        tow_delay_ticks=0)
    sc = _scen([_u("r1", "tk", 100.0, 100.0)], [_u("b1", "tk", 300.0, 100.0)],
               {"tk": _pt(pk=0.5)}, sustain=sus_spec, duration=10)
    k = MesaKernel(sc, seed=21)
    for _ in range(6):
        k.step()
    snap, _ = k.log.to_frames()
    assert {"damage_grade", "part", "disposition"} <= set(snap.columns)
    assert set(snap["damage_grade"].unique()) <= {0, 1, 2, 3}
    assert set(snap["disposition"].unique()) <= {0, 1, 2, 3, 4, 5}


# ----------------------------------------------------------------------
# 8. 交战日志分级 + 轻损/重度占比
# ----------------------------------------------------------------------

def test_k_kill_logs_grade(monkeypatch):
    import brigsim.combat.damage as dmg

    monkeypatch.setattr(dmg, "sample_part",
                        lambda role, ammo, rng, table=None: "hull")
    sus_spec = SustainmentSpec(part_stock={"red": {"mobility": 5}, "blue": {}},
                               tow_delay_ticks=0)
    sc = _unit_scen(sus_spec)
    k = MesaKernel(sc, seed=22)
    k.damage_model.rng = _Seq([0.05])
    killed, hit = k.damage_model.resolve_hit("shooter", 0, 0.5, "ap", "direct")
    assert killed is True and hit is True
    s = k.state
    assert not s.alive[0]
    assert int(s.damage_grade[0]) == GRADE_K
    assert str(s.part_id[0]) == "hull"
    assert int(s.disposition[0]) == DISP_WRITEOFF
    row = k.log.engagements[-1]
    assert row["killed"] is True and row["grade"] == "k"


def test_onsite_and_rear_fractions(monkeypatch):
    """命中段内：轻损害地处置 ≈0.30、重度后送 ≈0.15、其余 M 拖救（S12）。"""
    import brigsim.combat.damage as dmg

    monkeypatch.setattr(dmg, "sample_part",
                        lambda role, ammo, rng, table=None: "mobility")
    sus_spec = SustainmentSpec(part_stock={"red": {"mobility": 100}, "blue": {}},
                               tow_delay_ticks=0)
    sc = _unit_scen(sus_spec)
    k = MesaKernel(sc, seed=23)
    k.damage_model.rng = np.random.default_rng(0)
    s = k.state
    n = 2000
    onsite = rear = tow = 0
    for _ in range(n):
        k.damage_model.resolve_hit("s", 0, 1.0, "ap", "direct", always_log=False)
        d = int(s.disposition[0])
        onsite += d == DISP_ONSITE
        rear += d == DISP_REAR
        tow += d == DISP_TOW
    assert onsite + rear + tow == n
    assert abs(onsite / n - 0.30) <= 0.06
    assert abs(rear / n - 0.15) <= 0.06


# ----------------------------------------------------------------------
# 9. 修复时长：M（行走总成, repair 4）均值 > F（火控, repair 3）
# ----------------------------------------------------------------------

def _mean_duration(part: str, grade: int, disp: int, seed: int, n: int = 400) -> float:
    sus_spec = SustainmentSpec(
        part_stock={"red": {"mobility": 1000, "fire_control": 1000}, "blue": {}},
        tow_delay_ticks=0)
    sc = _unit_scen(sus_spec)
    state = SimState(sc)
    sus = SustainmentModel(sc, state, np.random.default_rng(seed))
    durs = []
    for _ in range(n):
        t0 = float(sus.env.now)
        done_before = sus.repairs_done[RED]
        sus.combat_damage(0, grade=grade, part=part, disposition=disp)
        sus.commit_combat_damage()
        sus.advance(t0 + 60.0)
        assert sus.repairs_done[RED] == done_before + 1
        ev = sus.event_frame()
        done_t = float(ev[ev["type"] == "repair_done"]["tick"].iloc[-1])
        durs.append(done_t - t0)
    return float(np.mean(durs))


def test_mobility_repair_takes_longer_than_fire_control():
    m_mean = _mean_duration("mobility", GRADE_M, DISP_TOW, seed=31)
    f_mean = _mean_duration("fire_control", GRADE_F, DISP_DRIVE_BACK, seed=32)
    assert m_mean - f_mean > 0.5  # E[exp(4)] - E[exp(3)] ≈ 1.0（软断言）


# ----------------------------------------------------------------------
# 10. 事件流 7 列且带等级/部件/处置
# ----------------------------------------------------------------------

def test_event_frame_columns_with_grade_fields():
    sus_spec = SustainmentSpec(part_stock={"red": {"mobility": 5}, "blue": {}},
                               tow_delay_ticks=0)
    sc = _unit_scen(sus_spec)
    state = SimState(sc)
    sus = SustainmentModel(sc, state, np.random.default_rng(61))
    sus.combat_damage(0, grade=GRADE_M, part="mobility", disposition=DISP_TOW)
    ev = sus.event_frame()
    assert list(ev.columns) == ["tick", "unit", "side", "type",
                                "grade", "part", "disposition"]
    row = ev[ev["type"] == "damage_combat"].iloc[0]
    assert int(row["grade"]) == GRADE_M
    assert str(row["part"]) == "mobility"
    assert int(row["disposition"]) == DISP_TOW
    other = ev[ev["type"] != "damage_combat"]
    if not other.empty:
        assert (other["side"] == RED).all()