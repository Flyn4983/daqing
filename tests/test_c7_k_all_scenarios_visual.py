"""C7-k 回归：全工况真实地形 + 真实空中模型 + rrd 新鲜度守卫。

运行：PYTHONPATH=src python -m pytest tests/test_c7_k_all_scenarios_visual.py -q
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import rerun as rr

from brigsim.data.provider import YamlFileProvider
from brigsim.kernel.terrain import HeightField
from brigsim.viz import rerun_view as rv

REPO = Path(__file__).resolve().parents[1]
SCENARIOS = [
    "example_company", "medium_brigade", "brigade_canyon_clear",
    "brigade_plain_fog", "brigade_rolling_fortified",
    "example_brigade", "example_battalion", "real_terrain_brigade",
]


@pytest.mark.parametrize("name", SCENARIOS)
def test_all_scenarios_use_real_dem_in_visualization(name):
    sc = YamlFileProvider(str(REPO / "scenarios")).load_scenario(name)
    orig = HeightField.from_spec(sc.terrain)
    vis = rv._visual_terrain(orig, name)
    assert vis is not orig, f"{name} 可视化地形未被替换为真实 DEM"
    assert vis.heights.min() > 500, f"{name} 地形高程不像太行山真实 DEM"
    assert vis.heights.max() > 900, f"{name} 地形最高点不像太行山真实 DEM"


def test_eight_scenarios_have_eight_distinct_maps():
    """用户反馈"八种工况还是同一张地图"：可见地形必须逐个不同。"""
    sigs = {}
    for name in SCENARIOS:
        sc = YamlFileProvider(str(REPO / "scenarios")).load_scenario(name)
        vis = rv._visual_terrain(HeightField.from_spec(sc.terrain), name)
        sigs[name] = hash(vis.heights.tobytes())
    assert len(set(sigs.values())) == len(SCENARIOS), (
        f"仍有工况共用同一张可见地形：{sigs}"
    )


def test_ground_models_use_lite_glb_and_are_scaled():
    from brigsim.viz import models

    assert models.USE_LITE_GROUND_MODELS
    for typ in models.LITE_TYPES:
        p = models.ensure_model(typ)
        assert p.parent.name == "lite" and p.is_file()
        assert p.stat().st_size < 100_000, f"{typ} 低面数模型仍过大：{p.stat().st_size}"
    assert rv.GROUND_MODEL_SCALE >= 8.0


def test_preload_air_models_logs_static_asset_per_asset(monkeypatch):
    logged = []
    orig = rv.rr.log

    def spy(entity, data, **kw):
        logged.append((entity, data, kw))

    monkeypatch.setattr(rv.rr, "log", spy)
    got = rv._preload_air_models([("ruau00", "uav"), ("bloi01", "loitering")])
    monkeypatch.setattr(rv.rr, "log", orig)
    assert got == {"ruau00:uav", "bloi01:loitering"}
    entities = [e for e, _, kw in logged if kw.get("static")]
    assert "world/air/ruau00/model" in entities
    assert "world/air/bloi01/model" in entities
    assert all(isinstance(d, rr.Asset3D) for _, d, _ in logged)


def test_all_air_packages_have_matching_rrd_entities():
    """每个工况的 rrd 必须含全部地面/空中真实模型实体（防止旧 rrd 混入）。"""
    import re

    air_pat = re.compile(rb"world/air/[A-Za-z0-9_-]+/model")
    gnd_pat = re.compile(rb"world/units/[A-Za-z0-9_-]+/model")
    from brigsim.dashboard.build import DEFAULT_PATHS, KNEE_X, load_knee_vector
    from brigsim.surrogate.design import apply_features

    for name in SCENARIOS:
        sc = YamlFileProvider(str(REPO / "scenarios")).load_scenario(name)
        x = load_knee_vector(DEFAULT_PATHS["pareto"]) or KNEE_X
        sc = apply_features(sc, x)
        expected_air = len(getattr(sc, "air_packages", []) or [])
        expected_ground = sum(len(sc.forces.get(s, [])) for s in ("red", "blue"))
        rrd = REPO / "outputs" / "dash" / f"replay_{name}.rrd"
        assert rrd.is_file(), f"缺少 {name} rrd"
        blob = rrd.read_bytes()
        got_air = len(set(air_pat.findall(blob)))
        got_ground = len(set(gnd_pat.findall(blob)))
        assert got_air == expected_air, (
            f"{name} rrd 空中模型实体 {got_air} != 想定 {expected_air}（疑似旧文件）"
        )
        assert got_ground >= expected_ground, (
            f"{name} rrd 地面模型实体 {got_ground} < 想定 {expected_ground}（仍有盒体/点球）"
        )
