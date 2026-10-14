"""M1 行为测试：Mesa 交战内核的侦察/C2/冷却与 SimPy 保障模型。"""

import numpy as np
import pandas as pd

from brigsim.combat.mesa_kernel import RED, MesaKernel
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
from brigsim.experiment.runner import run_once
from brigsim.kernel.state import SimState
from brigsim.sustainment.repair import SustainmentModel


def _load():
    return YamlFileProvider("scenarios").load_scenario("example_company")


def test_reload_cooldown_respected():
    """同一射手两次射击间隔不得小于装填 tick 数（示例想定全部 reload=2）。"""
    sc = _load()
    _, kernel = run_once(sc, seed=20261013, kernel_cls=MesaKernel)
    _, eng = kernel.log.to_frames()
    assert not eng.empty
    reload_by_type = {t: p.reload_ticks for t, p in sc.platform_types.items()}
    type_by_unit = {u.unit_id: u.type for side in sc.forces.values() for u in side}
    for shooter, grp in eng.groupby("shooter"):
        ticks = grp["tick"].to_numpy()
        gaps = np.diff(ticks)
        assert (gaps >= reload_by_type[type_by_unit[shooter]]).all()


def test_determinism_same_seed():
    sc = _load()
    _, k1 = run_once(sc, seed=4242, kernel_cls=MesaKernel)
    _, k2 = run_once(sc, seed=4242, kernel_cls=MesaKernel)
    sn1, en1 = k1.log.to_frames()
    sn2, en2 = k2.log.to_frames()
    pd.testing.assert_frame_equal(sn1, sn2)
    pd.testing.assert_frame_equal(en1, en2)


def test_under_repair_unit_immobile():
    sc = _load()
    kernel = MesaKernel(sc, seed=7)
    s = kernel.state
    i = int(np.where((s.side == RED) & s.alive)[0][0])
    s.under_repair[i] = True
    p0 = (s.x[i], s.y[i])
    kernel.step()
    assert (s.x[i], s.y[i]) == p0  # 在修单位本 tick 不机动


def test_combat_damage_gets_repaired():
    """战斗受损进入抢修通道，充足时间后必须归建（damaged/under_repair 清零）。"""
    sc = _load()
    state = SimState(sc)
    sustain = SustainmentModel(sc, state, np.random.default_rng(0))
    sustain.combat_damage(0)
    assert state.damaged[0] and not state.under_repair[0]  # tick 内只登记
    sustain.commit_combat_damage()  # tick 末统一入抢修通道
    assert state.under_repair[0]
    sustain.advance(50)  # MTTR=6，50 tick 内首轮修复必完成（远早于下次自然故障）
    assert not state.damaged[0]
    assert not state.under_repair[0]


def _single_unit_scenario(mtbf: float, mttr: float, duration: int) -> Scenario:
    return Scenario(
        name="sustain_unit",
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


def test_natural_failure_and_repair_cycle():
    """高频故障下应同时观察到「故障入修」与「修复归建」两个状态转换。"""
    sc = _single_unit_scenario(mtbf=2.0, mttr=1.0, duration=200)
    state = SimState(sc)
    sustain = SustainmentModel(sc, state, np.random.default_rng(1))
    saw_damaged = saw_cleared = False
    t = 0.0
    while t < 60.0:
        t += 0.2
        sustain.advance(t)
        if state.under_repair[0]:
            saw_damaged = True
        elif saw_damaged:
            saw_cleared = True
    assert saw_damaged and saw_cleared
    assert state.alive[0]


def test_no_repair_team_stays_damaged():
    """无抢修组（容量 0）时受损单位始终无法归建。"""
    sc = _single_unit_scenario(mtbf=1.0, mttr=1.0, duration=50)
    sc2 = sc.model_copy(update={"sustainment": SustainmentSpec(repair_teams={"red": 0, "blue": 0})})
    state = SimState(sc2)
    sustain = SustainmentModel(sc2, state, np.random.default_rng(2))
    sustain.combat_damage(0)
    sustain.commit_combat_damage()
    sustain.advance(100)
    assert state.damaged[0] and state.under_repair[0]
