"""C7-a0 合成兵种扩展测试。

两部分：
1. 机制单测：在最小合成想定上逐项验证间瞄延迟/面杀伤、BLOS 目标指示链、
   防空敌我识别与雷达引导、雷达/通信干扰、C2 光环、烟幕光电遮蔽、假目标、
   工兵破障/触雷、反炮兵定位、巡飞弹反辐射猎杀、恶劣天候禁飞、气象矩阵、通视。
2. 消融实验：在完整 example_brigade 想定上成对（同种子）抽掉关键力量，
   完整想定只做确定性结构断言（去工兵→无破障、去烟幕→无烟幕、去防空→无拦截），
   浓雾/通信压制做配对方向比较；工兵破障与烟幕防护的方向性在受控微想定上做
   机制隔离断言（v4 复核：全想定随机弱断言对平衡调整脆弱，a1/v4 两轮失效）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from brigsim.combat import weather
from brigsim.combat.mesa_kernel import MesaKernel
from brigsim.combat.systems import AirAsset, BattleSystems
from brigsim.data.provider import YamlFileProvider
from brigsim.data.schema import (
    BattlefieldSpec,
    Environment,
    ObstacleSpec,
    PlatformTypeSpec,
    Scenario,
    SmokeOrder,
    Terrain,
    UnitSpec,
)
from brigsim.kernel.terrain import HeightField

FLAT = Terrain(source="synthetic", size_m=(2000.0, 2000.0), grid_n=20,
               base_height=0.0, amplitude=0.0, water_level=0.0, noise_seed=1)
CLEAR = Environment(weather="clear", illumination="day")


def _pt(role: str = "assault", **kw) -> PlatformTypeSpec:
    """生成测试用平台类型；缺省为近距离直瞄单位。"""
    base = dict(
        sensor_range_m=1000.0, weapon_range_m=1000.0, pk=1.0,
        speed_m_per_tick=100.0, reload_ticks=1, detect_prob=1.0,
        track_ttl_ticks=5, role=role,
    )
    base.update(kw)
    return PlatformTypeSpec(**base)


def _scen(red: list[UnitSpec], blue: list[UnitSpec], types: dict,
          env: Environment = CLEAR, terrain: Terrain = FLAT,
          duration: int = 20, battlefield=None, air=None) -> Scenario:
    return Scenario(
        name="c7a0_test",
        mission={"duration_ticks": duration, "dt_seconds": 30,
                 "objective": [9999, 9999], "success_radius_m": 10},
        terrain=terrain,
        environment=env,
        platform_types=types,
        forces={"red": red, "blue": blue},
        battlefield=battlefield or BattlefieldSpec(),
        air_packages=air or [],
    )


def _u(uid: str, typ: str, x: float, y: float, **kw) -> UnitSpec:
    return UnitSpec(unit_id=uid, type=typ, pos=(x, y), **kw)


def _track(sysm: BattleSystems, side: int, target: int, src: str = "assault",
           recog: bool = False, ttl: int = 999) -> None:
    sysm.k.tracks[side][target] = {"exp": sysm.s.tick + ttl, "src": src, "recog": recog}


# ----------------------------------------------------------------------
# 1. 气象衰减矩阵
# ----------------------------------------------------------------------

def test_weather_matrix_channels_and_grounding():
    clear = weather.sensor_factors(Environment(weather="clear", illumination="day"))
    assert clear["optical"] == 1.0 and clear["radar"] == 1.0
    fog = weather.sensor_factors(Environment(weather="fog", illumination="day"))
    assert fog["optical"] < 0.3 and fog["radar"] > 0.95  # 雾遮光电不遮雷达
    night = weather.sensor_factors(Environment(weather="clear", illumination="night"))
    assert night["optical"] < 0.3 and night["ir"] > 0.8  # 夜暗压光学，红外几乎不受影响
    storm = weather.sensor_factors(Environment(weather="heavy_rain", illumination="day"))
    assert storm["radar"] < 1.0  # 大雨有雨衰
    assert weather.air_grounded(Environment(weather="heavy_rain"))
    assert weather.air_grounded(Environment(weather="heavy_fog"))
    assert not weather.air_grounded(CLEAR)
    assert weather.ground_move_factor(Environment(weather="snow")) < 1.0


# ----------------------------------------------------------------------
# 2. 地形通视
# ----------------------------------------------------------------------

def test_los_ridge_blocks_sightline():
    n = 21
    h = np.zeros((n, n))
    h[:, n // 2] = 300.0  # 中央南北向山脊
    hf = HeightField((2000.0, 2000.0), n, h, water_level=0.0)
    assert not hf.has_los(100.0, 1000.0, 1900.0, 1000.0)
    assert hf.has_los(100.0, 200.0, 800.0, 200.0)  # 山脊同侧通视


# ----------------------------------------------------------------------
# 3. 间瞄炮兵：呼叫延迟、面杀伤、弹药基数
# ----------------------------------------------------------------------

def test_indirect_fire_delay_splash_and_ammo():
    types = {"gun": _pt("artillery", fire_mode="indirect", weapon_range_m=5000,
                        pk=1.0, splash_radius_m=200, call_fire_delay_ticks=2,
                        ammo_basic_load=1),
             "tgt": _pt()}
    red, blue = [_u("g1", "gun", 1000, 5000)], [
        _u("v1", "tgt", 1500, 5000),   # 弹着中心
        _u("v2", "tgt", 1500, 5300),   # 300m，在 200m 杀伤半径外
    ]
    # 用大平地（10km）避免坐标越界
    sc = _scen(red, blue, types, terrain=Terrain(
        source="synthetic", size_m=(10000.0, 10000.0), grid_n=20,
        base_height=0, amplitude=0))
    k = MesaKernel(sc, seed=1)
    sysm = k.sys
    k.state.tick = 1
    sysm.pre_combat()
    _track(sysm, 0, 1)
    sysm.step_fires()
    assert len(sysm.missions) == 1 and sysm.missions[0].impact_tick == 3
    assert k.state.alive[1] and k.state.alive[2]  # 弹着前均存活
    assert k.state.ammo[0] == 0
    # 第 3 tick 弹着
    k.state.tick = 3
    sysm.step_fires()
    assert not k.state.alive[1]      # 半径内击毁（pk=1.0）
    assert k.state.alive[2]          # 半径外无伤
    k.state.cooldown[0] = 0          # 冷却结束（手动推进中无内核 step）
    k.state.tick = 4
    sysm.step_fires()
    assert len(sysm.missions) == 0   # 弹药基数耗尽，不再发射


def test_indirect_dug_in_halves_pk():
    """掩体目标按 0.5 pk 结算：高基数重复下击毁率显著低于暴露目标。"""
    types = {"gun": _pt("artillery", fire_mode="indirect", weapon_range_m=5000,
                        pk=0.8, splash_radius_m=50),
             "open": _pt(), "dug": _pt()}
    kills_open = kills_dug = 0
    trials = 200
    for r in range(trials):
        red = [_u("g1", "gun", 1000, 5000)]
        blue = [_u("vo", "open", 1500, 5000), _u("vd", "dug", 1500, 5050, dug_in=True)]
        sc = _scen(red, blue, types, terrain=Terrain(
            source="synthetic", size_m=(10000.0, 10000.0), grid_n=20,
            base_height=0, amplitude=0))
        k = MesaKernel(sc, seed=1000 + r)
        k.state.tick = 2
        k.sys.pre_combat()
        _track(k.sys, 0, 1)
        _track(k.sys, 0, 2)
        k.sys.step_fires()  # 发射（floor 延迟 1 tick）
        k.state.tick = 3
        k.sys.step_fires()  # 弹着结算
        if not k.state.alive[1]:
            kills_open += 1
        if not k.state.alive[2]:
            kills_dug += 1
    assert kills_open > kills_dug * 1.3


# ----------------------------------------------------------------------
# 4. BLOS 必须有目标指示链
# ----------------------------------------------------------------------

def test_blos_requires_designation_source():
    types = {"missile": _pt("missile", fire_mode="blos", weapon_range_m=9000,
                            pk=1.0, call_fire_delay_ticks=1, ammo_basic_load=2),
             "tgt": _pt()}
    sc = _scen([_u("m1", "missile", 1000, 5000)], [_u("v1", "tgt", 5000, 5000)], types,
               terrain=Terrain(source="synthetic", size_m=(10000.0, 10000.0),
                               grid_n=20, base_height=0, amplitude=0))
    k = MesaKernel(sc, seed=2)
    k.state.tick = 1
    k.sys.pre_combat()
    _track(k.sys, 0, 1, src="assault")  # 普通直瞄单位航迹不算目标指示
    k.sys.step_fires()
    assert len(k.sys.missions) == 0
    _track(k.sys, 0, 1, src="recon")    # 侦察来源 → 发射
    k.state.tick = 2
    k.sys.step_fires()
    assert len(k.sys.missions) == 1 and k.sys.missions[0].weapon == "blos"


# ----------------------------------------------------------------------
# 5. 防空：敌我识别 + 雷达引导/被干扰拦截率
# ----------------------------------------------------------------------

def _air(asset_id: str, side: int, x: float, y: float, kind: str = "loitering"):
    return AirAsset(aid=asset_id, side=side, kind=kind, launch_tick=0, waypoints=[],
                    alt=1500.0, loiter_total=1, seek=3000.0, weapon_pk=1.0,
                    shots_left=1, sensor_range=0.0, sensor_kind="fused", label="t",
                    state="loitering", x=x, y=y)


def test_air_defense_iff_never_fires_on_friendlies():
    types = {"ad": _pt("air_defense", coverage_radius_m=5500, weapon_range_m=0,
                       sensor_range_m=100, ammo_basic_load=99)}
    sc = _scen([_u(f"a{i}", "ad", 1000, 5000) for i in range(10)], [], types)
    k = MesaKernel(sc, seed=3)
    k.sys.air = [_air(f"rlo{i:02d}", 0, 1000, 5000) for i in range(10)]  # 己方空中
    k.state.tick = 1
    k.sys.pre_combat()
    k.sys._air_intercept()
    assert k.sys.intercepts == []
    assert all(a.state == "loitering" for a in k.sys.air)


def test_air_defense_guided_and_jammed_scaling():
    n = 60

    def run(radar: bool, jammed: bool, seed: int) -> int:
        types = {"ad": _pt("air_defense", coverage_radius_m=5500, weapon_range_m=0,
                           sensor_range_m=100, reload_ticks=99, ammo_basic_load=99),
                 "rad": _pt("radar_air", coverage_radius_m=100, sensor_range_m=100,
                            weapon_range_m=0, emission_duty=1.0),
                 "ew": _pt("ew", coverage_radius_m=9000, sensor_range_m=0,
                           weapon_range_m=0, jam_strength=1.0, jam_mode="radar")}
        red = [_u(f"a{i}", "ad", 1000, 5000) for i in range(n)]
        blue = []
        if radar:
            red.append(_u("rr1", "rad", 500, 500))
        if jammed:
            blue.append(_u("bj1", "ew", 1000, 5000))
        sc = _scen(red, blue, types)
        k = MesaKernel(sc, seed=seed)
        k.sys.air = [_air(f"blo{i:02d}", 1, 1000, 5000) for i in range(n)]
        k.state.tick = 1
        k.sys.pre_combat()
        k.sys._air_intercept()
        return len(k.sys.intercepts)

    guided = run(radar=True, jammed=False, seed=4)
    unguided = run(radar=False, jammed=False, seed=4)
    jammed = run(radar=True, jammed=True, seed=4)
    assert guided >= 25          # p=0.55 → 期望 33
    assert unguided <= 18        # p=0.22 → 期望 13
    assert jammed <= 22          # p=0.275 → 期望 16.5


# ----------------------------------------------------------------------
# 6. 电磁：雷达致盲 / 通信断链 / 链断航迹即时过期
# ----------------------------------------------------------------------

def test_ew_radar_and_comms_jamming():
    types = {
        "rad": _pt("radar_air", coverage_radius_m=100, sensor_range_m=100,
                   weapon_range_m=0),
        "grd": _pt("c2", coverage_radius_m=100, sensor_range_m=100,
                   weapon_range_m=0),
        "ewr": _pt("ew", jam_mode="radar", jam_strength=1.0, coverage_radius_m=9000,
                   sensor_range_m=0, weapon_range_m=0),
        "ewc": _pt("ew", jam_mode="comms", jam_strength=1.0, coverage_radius_m=9000,
                   sensor_range_m=0, weapon_range_m=0),
    }
    # 雷达干扰车 → 敌雷达被致盲，不影响通信
    sc = _scen([_u("e1", "ewr", 100, 100)], [_u("r1", "rad", 500, 100),
                                              _u("c1", "grd", 500, 200)], types)
    k = MesaKernel(sc, seed=5)
    k.state.tick = 1
    k.sys.pre_combat()
    assert k.sys.jammed_radar[list(k.state.unit_ids).index("r1")]
    assert not k.sys.comms_denied[list(k.state.unit_ids).index("c1")]
    # 通信干扰车 → 断链但不致盲雷达
    sc2 = _scen([_u("e2", "ewc", 100, 100)], [_u("r2", "rad", 500, 100),
                                               _u("c2", "grd", 500, 200)], types)
    k2 = MesaKernel(sc2, seed=5)
    k2.state.tick = 1
    k2.sys.pre_combat()
    assert k2.sys.comms_denied[list(k2.state.unit_ids).index("c2")]
    assert not k2.sys.jammed_radar[list(k2.state.unit_ids).index("r2")]


def test_comms_denied_track_expires_immediately():
    sc = _scen([_u("a", "tank", 0, 0)], [_u("b", "tank", 100, 0)],
               {"tank": _pt()})
    k = MesaKernel(sc, seed=6)
    k.state.tick = 10
    k.sys.add_track(0, 1, ttl=5, source="recon", link_denied=True)
    assert k.tracks[0][1]["exp"] == 10
    k._purge_tracks()
    assert 1 not in k.tracks[0]


# ----------------------------------------------------------------------
# 7. C2 光环（含被通信压制时失效）
# ----------------------------------------------------------------------

def test_c2_aura_bonus_and_suppression():
    types = {"c2": _pt("c2", aura_bonus=0.2, coverage_radius_m=1000,
                       sensor_range_m=0, weapon_range_m=0),
             "ew": _pt("ew", jam_mode="comms", jam_strength=1.0,
                       coverage_radius_m=9000, sensor_range_m=0, weapon_range_m=0),
             "tk": _pt()}
    red = [_u("hq", "c2", 500, 500), _u("t_in", "tk", 800, 500),
           _u("t_out", "tk", 5000, 5000)]
    sc = _scen(red, [], types,
               terrain=Terrain(source="synthetic", size_m=(10000.0, 10000.0),
                               grid_n=20, base_height=0, amplitude=0))
    k = MesaKernel(sc, seed=7)
    k.state.tick = 1
    k.sys.pre_combat()
    idx = {u: i for i, u in enumerate(k.state.unit_ids)}
    assert k.sys.pk_mult[idx["t_in"]] == pytest.approx(1.2)
    assert k.sys.det_mult[idx["t_out"]] == 1.0
    # 蓝通信对抗车压制后光环消失
    sc2 = _scen(red, [_u("be", "ew", 500, 500)], types,
                terrain=Terrain(source="synthetic", size_m=(10000.0, 10000.0),
                                grid_n=20, base_height=0, amplitude=0))
    k2 = MesaKernel(sc2, seed=7)
    k2.state.tick = 1
    k2.sys.pre_combat()
    assert k2.sys.pk_mult[list(k2.state.unit_ids).index("t_in")] == 1.0


# ----------------------------------------------------------------------
# 8. 烟幕：遮蔽光学/红外，不遮蔽雷达
# ----------------------------------------------------------------------

def _smoke_kernel(sensor_kw: str):
    types = {"eye": _pt(sensor_kind=sensor_kw, sensor_range_m=5000),
             "tgt": _pt()}
    sc = _scen([_u("eye", "eye", 490, 500)], [_u("tg", "tgt", 510, 500)], types)
    k = MesaKernel(sc, seed=8)
    from brigsim.combat.systems import _Smoke
    k.sys.active_smoke = [_Smoke(vehicle_idx=-1, x=500, y=500, radius=200,
                                 start=0, duration=99)]
    k.state.tick = 1
    k.agents.do("sense")
    return k


def test_smoke_blocks_optical_but_not_radar():
    assert 1 not in _smoke_kernel("optical").tracks[0]
    k = _smoke_kernel("radar")
    assert 1 in k.tracks[0]


# ----------------------------------------------------------------------
# 9. 假目标：未识破诱导弹药，识破后不再被选
# ----------------------------------------------------------------------

def test_decoy_lures_until_recognized():
    # pk=0：首发不毁伤目标（假目标保持存活），从而把"识破→不再发射"隔离验证
    types = {"m": _pt("missile", fire_mode="blos", weapon_range_m=9000, pk=0.0,
                      call_fire_delay_ticks=1, ammo_basic_load=3),
             "decoy": _pt()}
    sc = _scen([_u("m1", "m", 1000, 5000)], [_u("d1", "decoy", 5000, 5000, decoy=True)],
               types, terrain=Terrain(source="synthetic", size_m=(10000.0, 10000.0),
                                      grid_n=20, base_height=0, amplitude=0))
    k = MesaKernel(sc, seed=9)
    k.state.tick = 1
    k.sys.pre_combat()
    _track(k.sys, 0, 1, src="uav")
    k.sys.step_fires()
    assert len(k.sys.missions) == 1 and k.sys.missions[0].target_idx == 1
    # 侦察抵近识破后：候选只剩已识别假目标 → 冷却后也不再发射
    # （第一发导弹在 tick 4 弹着，旧任务出队；断言 tick 4 没有新发射）
    k.sys.mark_recog(0, 1)
    k.state.cooldown[0] = 0
    k.state.tick = 4
    k.sys.step_fires()
    ef = k.log.effect_frame()
    assert not ((ef["kind"] == "fire_launch") & (ef["tick"] == 4)).any()


# ----------------------------------------------------------------------
# 10. 工兵破障 + 雷场损伤
# ----------------------------------------------------------------------

def test_breach_progress_and_mine_damage():
    types = {"eng": _pt("engineer", sensor_range_m=100, weapon_range_m=0)}
    bf = BattlefieldSpec(obstacles=[
        ObstacleSpec(kind="dragons_teeth", center=(600, 500), size=(100, 100),
                     breach_ticks=6),
        ObstacleSpec(kind="minefield", center=(1400, 500), size=(100, 100),
                     breach_ticks=6),
    ])
    sc = _scen([_u("e1", "eng", 555, 500)], [], types, battlefield=bf)
    k = MesaKernel(sc, seed=10)
    k.state.tick = 1
    ob = k.sys.obstacles[0]
    for _ in range(5):
        k.sys.progress_breach()
    assert not ob.breached and ob.progress == 5
    k.sys.progress_breach()
    assert ob.breached
    ef = k.log.effect_frame()
    assert (ef["kind"] == "breach").any()
    # 雷场触雷分布：约 3% 击毁 / 9% 损伤
    rng = np.random.default_rng(0)
    k.sys.rng = rng
    killed = damaged = 0
    trials = 4000
    for _ in range(trials):
        before = len(k.log.engagements)
        k.sys.mine_damage(0, 1400, 500)
        if len(k.log.engagements) > before:
            row = k.log.engagements[-1]
            killed += row["killed"]
            damaged += row["damaged"]
    assert 0.015 < killed / trials < 0.05
    assert 0.055 < damaged / trials < 0.13


# ----------------------------------------------------------------------
# 11. 反炮兵雷达：捕获发射阵位
# ----------------------------------------------------------------------

def test_counter_battery_radar_locates_firer():
    types = {"gun": _pt("artillery", fire_mode="indirect", weapon_range_m=9000,
                        splash_radius_m=100, ammo_basic_load=2),
             "rcb_on": _pt("radar_cb", coverage_radius_m=9000, sensor_range_m=0,
                           weapon_range_m=0, emission_duty=1.0),
             "rcb_off": _pt("radar_cb", coverage_radius_m=9000, sensor_range_m=0,
                            weapon_range_m=0, emission_duty=0.0),
             "tgt": _pt()}
    ter = Terrain(source="synthetic", size_m=(10000.0, 10000.0), grid_n=20,
                  base_height=0, amplitude=0)
    sc = _scen([_u("g1", "gun", 1000, 5000)],
               [_u("rb1", "rcb_on", 8000, 5000), _u("v1", "tgt", 1500, 5000)], types,
               terrain=ter)
    k = MesaKernel(sc, seed=11)
    k.state.tick = 1
    k.sys.pre_combat()
    _track(k.sys, 0, 2)
    k.sys.step_fires()
    assert 0 in k.tracks[1] and k.tracks[1][0]["src"] == "radar_cb"
    # 静默雷达：不捕获
    sc2 = _scen([_u("g2", "gun", 1000, 5000)],
                [_u("rb2", "rcb_off", 8000, 5000), _u("v2", "tgt", 1500, 5000)],
                types, terrain=ter)
    k2 = MesaKernel(sc2, seed=11)
    k2.state.tick = 1
    k2.sys.pre_combat()
    _track(k2.sys, 0, 2)
    k2.sys.step_fires()
    assert 0 not in k2.tracks[1]


# ----------------------------------------------------------------------
# 12. 巡飞弹：反辐射优先猎杀开机雷达
# ----------------------------------------------------------------------

def test_loitering_prioritizes_emitting_radar():
    types = {"rad": _pt("radar_air", coverage_radius_m=100, sensor_range_m=0,
                        weapon_range_m=0, emission_duty=1.0),
             "tk": _pt()}
    ter = Terrain(source="synthetic", size_m=(10000.0, 10000.0), grid_n=20,
                  base_height=0, amplitude=0)
    sc = _scen([_u("rr1", "rad", 5000, 5000), _u("t1", "tk", 5100, 5000)], [],
               types, terrain=ter)
    k = MesaKernel(sc, seed=12)
    k.state.tick = 1
    k.sys.pre_combat()  # duty=1 → 开机
    a = _air("blo00", 1, 5050, 5000)
    k.sys.air = [a]
    hit = k.sys._loiter_strike(a)
    assert hit
    assert not k.state.alive[list(k.state.unit_ids).index("rr1")]
    assert k.state.alive[list(k.state.unit_ids).index("t1")]
    # 雷达静默时改打突击车辆
    sc2 = _scen([_u("rr2", "rad", 5000, 5000), _u("t2", "tk", 5100, 5000)], [],
                types, terrain=ter)
    k2 = MesaKernel(sc2, seed=12)
    k2.state.tick = 1
    k2.sys.pre_combat()
    k2.state.radar_on[:] = False
    a2 = _air("blo01", 1, 5050, 5000)
    k2.sys.air = [a2]
    k2.sys._loiter_strike(a2)
    assert k2.state.alive[list(k2.state.unit_ids).index("rr2")]
    assert not k2.state.alive[list(k2.state.unit_ids).index("t2")]


# ----------------------------------------------------------------------
# 13. 恶劣天候禁飞
# ----------------------------------------------------------------------

def test_air_grounded_in_storm():
    from brigsim.data.schema import AirPackage
    types = {"tk": _pt()}
    storm = Environment(weather="heavy_rain", illumination="day")
    air = [AirPackage(side="red", kind="uav", launch_tick=0,
                      waypoints=[(1000, 1000)], sensor_range_m=500)]
    sc = _scen([_u("t", "tk", 0, 0)], [], types, env=storm, air=air)
    k = MesaKernel(sc, seed=13)
    k.state.tick = 1
    k.sys.step_fires()
    assert k.sys.air == [] and len(k.sys.air_pending) == 1


# ----------------------------------------------------------------------
# 14. 完整想定消融实验（同种子配对，验证体系要素的方向性贡献）
# ----------------------------------------------------------------------

SEEDS = [101, 202, 303, 404, 505]


@pytest.fixture(scope="module")
def baseline():
    """模块级共享：基线想定与 5 个固定种子的基线指标（消融测试共用，避免重复推演）。"""
    sc = YamlFileProvider().load_scenario("example_brigade")
    # 烟幕消融的统计窗 = 屏烟起烟到消散后 8 tick（覆盖间瞄弹着延迟等余效）
    orders = sc.battlefield.smoke_orders
    window = (
        min(z.start_tick for z in orders),
        max(z.start_tick + z.duration_ticks for z in orders) + 8,
    )
    return {"sc": sc, "window": window,
            "m": [_run(sc, sd, window=window) for sd in SEEDS]}


def _run(sc: Scenario, seed: int, post_init=None, window=None) -> dict:
    k = MesaKernel(sc, seed=seed)
    if post_init is not None:
        post_init(k)
    while not k.finished:
        k.step()
    s, ef = k.state, k.log.effect_frame()
    roles = np.asarray(s.role, dtype=object)
    alive_assault = s.alive & (s.side == 0) & (roles == "assault")
    eng = pd.DataFrame(k.log.engagements)
    red_ids = {u.unit_id for u in sc.forces["red"]}
    mine_hits = window_kills = 0
    if len(eng):
        at_red = eng[eng["target"].isin(red_ids)]
        mine_hits = int((at_red["weapon"] == "mine").sum())
        if window is not None:
            lo, hi = window
            w = at_red[(at_red["weapon"] != "mine")
                       & (at_red["tick"] >= lo) & (at_red["tick"] <= hi)]
            window_kills = int(w["killed"].sum())
    return {
        "red_alive": int(s.alive[s.side == 0].sum()),
        "lead_x": float(s.x[alive_assault].max()) if alive_assault.any() else -1.0,
        "breaches": sum(1 for o in k.sys.obstacles if o.breached),
        "smoke": int((ef["kind"] == "smoke").sum()) if len(ef) else 0,
        "red_intercepts": int(
            ((ef["kind"] == "intercept") & (ef["side"] == 0)).sum()) if len(ef) else 0,
        "direct_shots": int((eng["weapon"] == "direct").sum()) if len(eng) else 0,
        "red_fire_launch": int(
            ((ef["kind"] == "fire_launch") & (ef["side"] == 0)).sum()) if len(ef) else 0,
        # C7-a1 后消融改用机制链最近的指标（见两个消融测试内注释）
        "mine_hits": mine_hits,
        "window_kills": window_kills,
    }


def _majority(baseline: list, ablated: list, cmp) -> bool:
    return sum(cmp(a, b) for a, b in zip(baseline, ablated, strict=True)) >= 4


FLAT10K = Terrain(source="synthetic", size_m=(10000.0, 10000.0), grid_n=20,
                  base_height=0.0, amplitude=0.0, water_level=0.0, noise_seed=1)

# v4（C7-a2）复核结论：完整想定上的随机弱方向断言对平衡调整天然脆弱
# （a1/v4 两轮平衡后各失效一次）。工兵/烟幕的方向性贡献改为在受控微想定上
# 做机制隔离断言（无蓝军或单车固守，几何精确控制）；完整想定只保留确定性
# 结构断言（去工兵→0 破障、去烟幕→0 发烟事件）。


def _mine_lane_scenario(with_engineer: bool) -> Scenario:
    """微想定：8 辆冲击车多车道东进，必经一道未破障雷场（平地、无蓝军）。

    只隔离"工兵开辟通路→后续车辆不触雷/不减速"这一条机制链。
    """
    types = {
        "tk": _pt(role="assault", pk=0.0, sensor_range_m=10.0,
                  weapon_range_m=0.0, speed_m_per_tick=100.0),
        "eng": _pt(role="engineer", pk=0.0, sensor_range_m=10.0,
                   weapon_range_m=0.0, speed_m_per_tick=250.0),
    }
    red = [_u(f"t{i}", "tk", 0.0, 700.0 + 100.0 * i) for i in range(8)]
    if with_engineer:
        red.append(_u("e0", "eng", 0.0, 1000.0))
    bf = BattlefieldSpec(obstacles=[
        ObstacleSpec(kind="minefield", center=(1500.0, 1000.0),
                     size=(200.0, 4000.0), breach_ticks=2),
    ])
    return Scenario(
        name="c7a0_mine_lane",
        mission={"duration_ticks": 30, "dt_seconds": 30,
                 "objective": (9000.0, 1000.0), "success_radius_m": 10},
        terrain=FLAT10K, environment=CLEAR, platform_types=types,
        forces={"red": red, "blue": []}, battlefield=bf,
    )


def _mine_event_count(sc: Scenario, seed: int) -> int:
    k = MesaKernel(sc, seed=seed)
    while not k.finished:
        k.step()
    eng = pd.DataFrame(k.log.engagements)
    if not len(eng):
        return 0
    return int((eng["weapon"] == "mine").sum())


def test_ablation_engineers_required_for_breach(baseline):
    base = baseline["sc"]

    def no_eng() -> Scenario:
        sc = base.model_copy(deep=True)
        sc.forces["red"] = [
            u for u in sc.forces["red"]
            if sc.platform_types[u.type].role != "engineer"
        ]
        return sc

    m = [_run(no_eng(), sd) for sd in SEEDS]
    # 完整想定上的确定性断言：无工兵则任何障碍都不会被开辟
    assert all(x["breaches"] == 0 for x in m)
    # 方向性断言（受控微想定）：工兵在冲击车抵达前完成破障（t8 vs t14），
    # 已开辟通路无减速也无触雷；无工兵时 8 车以 0.2 速度蜗行 200m 雷场，
    # 每车多 tick 暴露，必现触雷。
    for sd in SEEDS:
        assert _mine_event_count(_mine_lane_scenario(True), sd) == 0
        assert _mine_event_count(_mine_lane_scenario(False), sd) >= 3


def _smoke_crossfire_scenario(with_smoke: bool) -> Scenario:
    """微想定：1 个掘壕直瞄火器守东端，5 辆无反击能力冲击车从其射界内通过。

    有烟幕档：烟幕带同时覆盖守军与通道，光电侦察完全断链；
    只隔离"屏烟遮光电→直瞄看不到"这一条机制链。
    """
    types = {
        "bg": _pt(role="assault", sensor_range_m=800.0, weapon_range_m=800.0,
                  pk=1.0, reload_ticks=1, detect_prob=1.0, speed_m_per_tick=0.0),
        "tk": _pt(role="assault", pk=0.0, sensor_range_m=10.0,
                  weapon_range_m=0.0, speed_m_per_tick=150.0),
        "sv": _pt(role="support", sensor_range_m=50.0, weapon_range_m=0.0,
                  speed_m_per_tick=0.0),
    }
    blue = [_u("bg0", "bg", 2000.0, 1000.0, dug_in=True)]
    red = [_u(f"t{i}", "tk", 1300.0 + 60.0 * i, 1000.0) for i in range(5)]
    orders = []
    if with_smoke:
        red.append(_u("sv0", "sv", 250.0, 1000.0))
        orders.append(SmokeOrder(unit_id="sv0", at=(1700.0, 1000.0),
                                 radius_m=450.0, start_tick=0,
                                 duration_ticks=20))
    return Scenario(
        name="c7a0_smoke_crossfire",
        mission={"duration_ticks": 8, "dt_seconds": 30,
                 "objective": (9000.0, 1000.0), "success_radius_m": 10},
        terrain=FLAT10K, environment=CLEAR, platform_types=types,
        forces={"red": red, "blue": blue},
        battlefield=BattlefieldSpec(smoke_orders=orders),
    )


def _red_direct_kills(sc: Scenario, seed: int) -> int:
    k = MesaKernel(sc, seed=seed)
    while not k.finished:
        k.step()
    eng = pd.DataFrame(k.log.engagements)
    if not len(eng):
        return 0
    red_ids = {u.unit_id for u in sc.forces["red"]}
    w = eng[eng["target"].isin(red_ids) & (eng["weapon"] == "direct")]
    return int(w["killed"].sum())


def test_ablation_smoke_screen_protects_assault(baseline):
    base = baseline["sc"]

    def no_smoke() -> Scenario:
        sc = base.model_copy(deep=True)
        sc.battlefield = BattlefieldSpec(obstacles=sc.battlefield.obstacles,
                                         smoke_orders=[])
        return sc

    m = [_run(no_smoke(), sd) for sd in SEEDS]
    # 完整想定上的确定性断言：无发烟命令则无烟幕事件
    assert all(x["smoke"] == 0 for x in m)
    # 方向性断言（受控微想定）：烟幕带覆盖守军光电传感器，其全程无共享航迹
    # → 0 击毁；无烟幕时守军 pk=1 逐点击毁，5 车全部战毁。
    for sd in SEEDS:
        assert _red_direct_kills(_smoke_crossfire_scenario(True), sd) == 0
        assert _red_direct_kills(_smoke_crossfire_scenario(False), sd) >= 4


def test_ablation_fog_cuts_direct_engagements(baseline):
    base, base_m = baseline["sc"], baseline["m"]

    def fog() -> Scenario:
        sc = base.model_copy(deep=True)
        sc.environment.weather = "heavy_fog"
        return sc

    m = [_run(fog(), sd) for sd in SEEDS]
    assert _majority([x["direct_shots"] for x in base_m],
                     [x["direct_shots"] for x in m], lambda a, b: a > b)


def test_ablation_red_air_defense(baseline):
    base = baseline["sc"]

    def no_ad() -> Scenario:
        sc = base.model_copy(deep=True)
        sc.forces["red"] = [
            u for u in sc.forces["red"]
            if sc.platform_types[u.type].role != "air_defense"
        ]
        return sc

    m = [_run(no_ad(), sd) for sd in SEEDS]
    assert all(x["red_intercepts"] == 0 for x in m)


def test_ablation_blue_comms_jamming_degrades_red_fires(baseline):
    base, base_m = baseline["sc"], baseline["m"]

    def force_jam(k: MesaKernel) -> None:
        s = k.state
        idx = np.where((s.side == 1) & (np.asarray(s.role, dtype=object) == "ew"))[0]
        s.jam_strength[idx] = 1.0
        s.coverage[idx] = 30000.0
        s.jam_mode[idx] = 1  # 全时段全谱通信压制

    m = [_run(base.model_copy(deep=True), sd, post_init=force_jam) for sd in SEEDS]
    assert _majority([x["red_fire_launch"] for x in base_m],
                     [x["red_fire_launch"] for x in m], lambda a, b: a >= b)
