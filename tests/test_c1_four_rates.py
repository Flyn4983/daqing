"""C1 四率对齐：GJB 口径公式的单测（事件计数、汇总口径、时序口径）。"""

import numpy as np

from brigsim.combat.mesa_kernel import MesaKernel
from brigsim.data.provider import YamlFileProvider
from brigsim.data.schema import (
    Environment,
    Mission,
    PlatformTypeSpec,
    Scenario,
    SustainmentSpec,
    Terrain,
    UnitSpec,
)
from brigsim.experiment.four_rates import four_rates_summary, four_rates_timeseries
from brigsim.experiment.runner import run_once
from brigsim.kernel.state import SimState
from brigsim.sustainment.repair import BLUE, RED, SustainmentModel


def _load():
    return YamlFileProvider("scenarios").load_scenario("example_company")


def _single_unit_scenario(mtbf: float, mttr: float, duration: int) -> Scenario:
    return Scenario(
        name="four_rates_unit",
        mission=Mission(duration_ticks=duration),
        terrain=Terrain(grid_n=9, amplitude=0.0, base_height=20.0, water_level=0.0),
        environment=Environment(visibility_factor=1.0),
        platform_types={
            "tank": PlatformTypeSpec(
                sensor_range_m=10.0,
                weapon_range_m=10.0,
                pk=0.0,
                speed_m_per_tick=0.0,
                mtbf_ticks=mtbf,
                mttr_ticks=mttr,
            )
        },
        sustainment=SustainmentSpec(repair_teams={"red": 1, "blue": 1}),
        forces={"red": [UnitSpec(unit_id="r1", type="tank", pos=(100.0, 100.0))], "blue": []},
    )


def test_run_once_metrics_contain_four_rates():
    """run_once 指标字典必须带齐四率键，且取值域与包含关系正确。"""
    sc = _load()
    m, kernel = run_once(sc, seed=20261013, kernel_cls=MesaKernel)
    for side in ("red", "blue"):
        assert 0.0 <= m[f"repair_rate_{side}"] <= 1.0
        assert m[f"repairs_done_{side}"] <= m[f"repairs_required_{side}"]
        assert m[f"failures_natural_{side}"] >= 0.0
        assert m[f"failures_combat_{side}"] >= 0.0
        assert 0.0 <= m[f"availability_mean_{side}"] <= 1.0
        assert m[f"failure_rate_{side}"] >= 0.0
        assert m[f"failure_rate_100h_{side}"] >= 0.0
    # 应修 ⊆ 故障+战损事件（同 tick 重复受损只入修一次，故为 ≤）
    s = kernel.sustain
    for side in (RED, BLUE):
        assert s.repairs_required[side] <= s.failures_natural[side] + s.failures_combat[side]
        assert s.repairs_done[side] <= s.repairs_required[side]


def test_four_rates_timeseries_ranges_and_monotonicity():
    """时序表：取值域 [0,1]；战损率与累计列单调不减；末行与汇总口径一致。"""
    sc = _load()
    m, kernel = run_once(sc, seed=20261013, kernel_cls=MesaKernel)
    snap, _ = kernel.log.to_frames()
    ts = four_rates_timeseries(kernel.state, snap, kernel.sustain.event_frame())
    for col in ("availability", "attrition", "repair_rate", "failure_rate"):
        assert ((ts[col] >= 0.0) & (ts[col] <= 1.0)).all(), col
    assert set(ts["side"].unique()) == {RED, BLUE}
    for side, name in ((RED, "red"), (BLUE, "blue")):
        sub = ts[ts["side"] == side].sort_values("tick")
        for col in ("attrition", "failures_natural_cum", "damage_combat_cum",
                    "repairs_required_cum", "repairs_done_cum"):
            assert (sub[col].diff().dropna() >= -1e-12).all(), col
        # 末行 = 终值口径（与 per_run_metrics 的 readiness/attrition 一致）
        last = sub.iloc[-1]
        assert abs(last["availability"] - m[f"readiness_{name}"]) < 1e-9
        assert abs(last["attrition"] - m[f"attrition_{name}"]) < 1e-9


def test_sustain_counters_natural_cycle():
    """高频自然故障：计数器、事件表与无快照表时的汇总口径。"""
    sc = _single_unit_scenario(mtbf=2.0, mttr=1.0, duration=200)
    state = SimState(sc)
    sustain = SustainmentModel(sc, state, np.random.default_rng(1))
    sustain.advance(60.0)
    assert sustain.failures_natural[RED] > 0
    assert sustain.failures_combat[RED] == 0
    assert sustain.repairs_required[RED] == sustain.failures_natural[RED]
    assert 0 < sustain.repairs_done[RED] <= sustain.repairs_required[RED]
    ev = sustain.event_frame()
    assert not ev.empty
    assert set(ev["type"].unique()) <= {"failure_natural", "repair_start", "repair_done"}
    assert (ev["side"] == RED).all()
    summ = four_rates_summary(state, sustain)
    assert 0.0 <= summ["repair_rate_red"] <= 1.0
    # 未提供快照表：不产出时序平均/暴露口径键
    assert "availability_mean_red" not in summ
    assert "failure_rate_red" not in summ


def test_combat_damage_counter_and_repair():
    """战斗受损计入战损事件与应修，修复完成后修复率 = 1。"""
    sc = _single_unit_scenario(mtbf=1e6, mttr=1.0, duration=100)
    state = SimState(sc)
    sustain = SustainmentModel(sc, state, np.random.default_rng(3))
    sustain.combat_damage(0)
    assert sustain.failures_combat[RED] == 1
    sustain.commit_combat_damage()
    assert sustain.repairs_required[RED] == 1
    sustain.advance(50.0)
    assert sustain.repairs_done[RED] == 1
    summ = four_rates_summary(state, sustain)
    assert summ["repair_rate_red"] == 1.0
    assert summ["failures_combat_red"] == 1.0
    assert summ["repairs_required_red"] == 1.0
