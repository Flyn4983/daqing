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
def test_all_scenarios_use_same_real_dem_in_visualization(name):
    sc = YamlFileProvider(str(REPO / "scenarios")).load_scenario(name)
    orig = HeightField.from_spec(sc.terrain)
    vis = rv._visual_terrain(orig)
    assert vis is not orig, f"{name} 可视化地形未被替换为真实 DEM"
    assert vis.preset_family is None, f"{name} 替换后仍带程序预设标记"
    assert vis.heights.min() > 500, f"{name} 地形高程不像太行山真实 DEM"
    assert vis.heights.max() > 1800, f"{name} 地形最高点不像太行山真实 DEM"


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
    """每个有空中架次的工况，其 rrd 必须含对应实体（防止旧 rrd 混入）。"""
    import re

    pat = re.compile(rb"world/air/[A-Za-z0-9_-]+/model")
    for name in SCENARIOS:
        sc = YamlFileProvider(str(REPO / "scenarios")).load_scenario(name)
        expected = len(getattr(sc, "air_packages", []) or [])
        if expected == 0:
            continue
        rrd = REPO / "outputs" / "dash" / f"replay_{name}.rrd"
        assert rrd.is_file(), f"缺少 {name} rrd"
        got = len(set(pat.findall(rrd.read_bytes())))
        assert got == expected, f"{name} rrd 空中模型实体 {got} != 想定 {expected}（疑似旧文件）"
