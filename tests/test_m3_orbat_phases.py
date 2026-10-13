"""M3：成建制模板展开（ORBAT）与多阶段任务推进测试。"""

import numpy as np
import pytest

from brigsim.combat.mesa_kernel import MesaKernel
from brigsim.data.provider import YamlFileProvider
from brigsim.data.schema import Mission, MissionPhase, PlatformTypeSpec, Scenario
from brigsim.experiment.runner import run_once

PT = {
    "tank": PlatformTypeSpec(
        sensor_range_m=2500, weapon_range_m=2000, pk=0.35, speed_m_per_tick=220
    ),
    "atgm_team": PlatformTypeSpec(
        sensor_range_m=2200, weapon_range_m=2000, pk=0.45, speed_m_per_tick=120
    ),
}


def _scenario(**kw) -> Scenario:
    base = dict(name="t", platform_types=PT)
    base.update(kw)
    return Scenario.model_validate(base)


def test_line_template_expands_centered_on_anchor():
    sc = _scenario(
        force_templates={
            "red": [
                {
                    "type": "tank",
                    "count": 5,
                    "layout": "line",
                    "anchor": [1000.0, 4000.0],
                    "heading_deg": 90,
                    "spacing_lateral": 300,
                    "org": "营·坦克连",
                    "id_prefix": "rt",
                }
            ]
        }
    )
    red = sc.forces["red"]
    assert len(red) == 5
    assert [u.unit_id for u in red] == [f"rt{i:02d}" for i in range(1, 6)]
    # 横队（向东）：x 全在锚点纵线，y 等间隔且均值=锚点
    assert all(u.pos[0] == 1000.0 for u in red)
    ys = sorted(u.pos[1] for u in red)
    assert np.allclose(np.diff(ys), 300)
    assert abs(np.mean(ys) - 4000.0) < 1e-9
    assert all(u.heading_deg == 90 for u in red)
    assert all(u.org.startswith("营·坦克连") for u in red)


def test_wedge_template_count_and_id_prefix_unique():
    sc = _scenario(
        force_templates={
            "red": [
                {
                    "type": "tank",
                    "count": 10,
                    "layout": "wedge",
                    "anchor": [0, 0],
                    "heading_deg": 90,
                    "spacing_along": 400,
                    "spacing_lateral": 350,
                    "org": "营·坦1",
                    "id_prefix": "a",
                },
                {
                    "type": "atgm_team",
                    "count": 4,
                    "layout": "line",
                    "anchor": [0, 5000],
                    "org": "营·火力",
                    "id_prefix": "b",
                },
            ]
        }
    )
    ids = [u.unit_id for u in sc.forces["red"]]
    assert len(ids) == 14 and len(set(ids)) == 14
    # 模板展开后清空，model_copy（灵敏度路径）不会二次展开
    assert sc.force_templates == {"red": [], "blue": []}
    copied = sc.model_copy()
    assert len(copied.forces["red"]) == 14


def test_explicit_units_kept_and_id_collision_rejected():
    sc = _scenario(
        forces={"red": [{"unit_id": "x01", "type": "tank", "pos": [0.0, 0.0]}], "blue": []},
        force_templates={
            "red": [
                {"type": "tank", "count": 2, "anchor": [500, 500], "org": "连", "id_prefix": "y"}
            ]
        },
    )
    assert {u.unit_id for u in sc.forces["red"]} == {"x01", "y01", "y02"}
    with pytest.raises(Exception):
        _scenario(
            forces={"red": [{"unit_id": "z01", "type": "tank", "pos": [0.0, 0.0]}], "blue": []},
            force_templates={
                "red": [
                    {"type": "tank", "count": 1, "anchor": [0, 0], "org": "连", "id_prefix": "z"}
                ]
            },
        )


def test_unknown_platform_type_rejected():
    with pytest.raises(Exception):
        _scenario(
            force_templates={
                "red": [
                    {"type": "ufo", "count": 1, "anchor": [0, 0], "org": "连", "id_prefix": "u"}
                ]
            }
        )


def test_company_scenario_single_phase_compat():
    sc = YamlFileProvider().load_scenario("example_company")
    assert len(sc.mission.phase_list) == 1
    assert sc.mission.phase_list[0].objective == (8000.0, 5000.0)
    assert len(sc.forces["red"]) == 7 and len(sc.forces["blue"]) == 5


def test_battalion_scenario_expands_44_vs_27_three_phases():
    sc = YamlFileProvider().load_scenario("example_battalion")
    assert len(sc.forces["red"]) == 44
    assert len(sc.forces["blue"]) == 27
    assert [p.name for p in sc.mission.phase_list] == [
        "占领进攻出发阵地",
        "突破前沿警戒阵地",
        "夺占纵深要点",
    ]


def test_multi_phase_progression_and_early_success():
    mission = Mission(
        duration_ticks=30,
        phases=[
            MissionPhase(name="p1", objective=(500.0, 0.0), success_radius_m=120),
            MissionPhase(name="p2", objective=(1000.0, 0.0), success_radius_m=120),
            MissionPhase(name="p3", objective=(1500.0, 0.0), success_radius_m=120),
        ],
    )
    sc = _scenario(
        mission=mission,
        forces={
            "red": [{"unit_id": "r1", "type": "tank", "pos": [0.0, 0.0], "heading_deg": 90}],
            "blue": [],
        },
    )
    metrics, kernel = run_once(sc, seed=1, kernel_cls=MesaKernel)
    assert metrics["success"] is True
    # 末段提前收束，不必跑满 30 tick
    assert kernel.state.tick < 30
    sn, _ = kernel.log.to_frames()
    assert set(sn["phase"].unique()) == {0, 1, 2}
    assert sn["phase"].max() == 2


def test_red_annihilation_early_finish_no_success():
    pt = {
        "tank": PlatformTypeSpec(
            sensor_range_m=3000,
            weapon_range_m=2500,
            pk=0.01,
            speed_m_per_tick=220,
            reload_ticks=1,
            detect_prob=1.0,
        ),
        "atgm_team": PlatformTypeSpec(
            sensor_range_m=3000,
            weapon_range_m=2500,
            pk=0.9,
            speed_m_per_tick=0,
            reload_ticks=1,
            detect_prob=1.0,
        ),
    }
    sc = Scenario.model_validate(
        {
            "name": "wipe",
            "platform_types": pt,
            "mission": {"duration_ticks": 200, "objective": [5000.0, 500.0]},
            "forces": {
                "red": [{"unit_id": "r1", "type": "tank", "pos": [0.0, 500.0]}],
                "blue": [
                    {
                        "unit_id": f"b{i}",
                        "type": "atgm_team",
                        "pos": [3000.0, 300.0 + i * 200.0],
                        "heading_deg": 270,
                    }
                    for i in range(4)
                ],
            },
        }
    )
    metrics, kernel = run_once(sc, seed=2, kernel_cls=MesaKernel)
    assert metrics["success"] is False
    assert kernel.finished
    assert kernel.state.tick < 200
    assert not kernel.state.alive[kernel.state.side == 0].any()
