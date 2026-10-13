"""M3：GeoTIFF DEM 地形适配器测试（GDAL 重采样、想定接入、路径解析）。"""

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from brigsim.data.provider import YamlFileProvider
from brigsim.data.schema import Terrain
from brigsim.kernel.terrain import HeightField


def _write_tif(path, n=24):
    """写一张线性斜坡 float32 DEM：h(x, y) = 100*x_idx + y_idx。"""
    h = (np.arange(n)[:, None] * 100 + np.arange(n)[None, :]).astype(np.float32)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=n,
        width=n,
        count=1,
        dtype="float32",
        crs="EPSG:32650",
        transform=from_origin(0, 0, 10, 10),
    ) as ds:
        ds.write(h, 1)


def _write_flat_dem_and_pass(path_dem, path_pass, n=24):
    """平坦高地 DEM（坡度 0、不涉水）+ 通行性栅格：
    一般地表 0.6；y 向河带 0.12；河带中央 x 向桥位恢复 1.0；北端 y<0.2 公路 1.0。
    """
    h = np.full((n, n), 100.0, dtype=np.float32)
    yy = (np.arange(n)[:, None] + 0.5) / n
    xx = (np.arange(n)[None, :] + 0.5) / n
    p = np.full((n, n), 0.6, dtype=np.float32)
    river_rows = ((yy[:, 0] >= 0.38) & (yy[:, 0] <= 0.62))[:, None]
    p[np.broadcast_to(river_rows, p.shape)] = 0.12
    bridge = river_rows & (xx >= 0.30) & (xx <= 0.70)
    p[bridge] = 1.0
    p[np.broadcast_to(yy < 0.2, p.shape)] = 1.0  # 沿河公路
    for path, band in ((path_dem, h), (path_pass, p)):
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            height=n,
            width=n,
            count=1,
            dtype="float32",
            crs="EPSG:32650",
            transform=from_origin(0, 0, 10, 10),
        ) as ds:
            ds.write(band, 1)


def test_from_geotiff_resamples_and_interpolates(tmp_path):
    tif = tmp_path / "dem.tif"
    _write_tif(tif)
    field = HeightField.from_geotiff(str(tif), size=(1000.0, 1000.0), grid_n=12, water_level=5)
    assert field.heights.shape == (12, 12)
    assert np.isfinite(field.heights).all()
    # 西南低、东北高（GDAL 重采样取邻域均值，角落值含首像元平均，取宽界）
    sw, ne = field.height_at(2.0, 2.0), field.height_at(998.0, 998.0)
    assert sw < ne
    assert sw < 300
    assert ne > 1800


def test_geotiff_spec_requires_path():
    with pytest.raises(ValueError, match="geotiff_path"):
        HeightField.from_spec(Terrain(source="geotiff"))


def test_battalion_scenario_uses_dem_and_resolves_relative_path():
    import os
    from pathlib import Path

    sc = YamlFileProvider().load_scenario("example_battalion")
    assert sc.terrain.source == "geotiff"
    geo = Path(sc.terrain.geotiff_path)
    assert geo.name == "dem_demo.tif" and geo.parent.name == "terrain"
    # Provider 已把相对路径解析为绝对路径，直接加载内核网格
    assert os.path.isabs(sc.terrain.geotiff_path)
    field = HeightField.from_spec(sc.terrain)
    assert field.heights.shape == (65, 65)
    assert np.isfinite(field.heights).all()
    # 合成峡谷：全域高程落在 DEM 设计区间内
    assert field.heights.min() >= 0.0
    assert field.heights.max() <= 420.0
    # 机动系数有限且在合法区间（含水面减速）
    f = field.move_factor(np.array([100.0, 5200.0]), np.array([5200.0, 100.0]))
    assert np.isfinite(f).all()
    assert float(f.min()) >= 0.05 and float(f.max()) <= 1.0


def test_passability_loads_and_shapes_move_factor(tmp_path):
    dem, pas = tmp_path / "dem.tif", tmp_path / "pass.tif"
    _write_flat_dem_and_pass(dem, pas)
    field = HeightField.from_geotiff(
        str(dem), size=(1000.0, 1000.0), grid_n=12, water_level=5,
        passability_path=str(pas),
    )
    assert field.passability.shape == (12, 12)
    xs = np.array([500.0, 100.0, 500.0, 900.0])
    ys = np.array([80.0, 500.0, 500.0, 800.0])
    f_road, f_river, f_bridge, f_ground = field.move_factor(xs, ys)
    # 平坦无坡、不涉水，机动系数即通行性插值（GDAL 降采样核会吃邻域，断言留余量）
    assert f_road > 0.95
    assert f_bridge > 0.9
    assert f_river < 0.2
    assert 0.5 < f_ground < 0.7
    # 桥梁相对无桥河面显著恢复通行
    assert f_bridge > 4.0 * f_river


def test_passability_defaults_to_ones(tmp_path):
    # 平坦 DEM、无通行性栅格：全域系数应只受坡度/水面影响
    dem = tmp_path / "flat.tif"
    h = np.full((24, 24), 100.0, dtype=np.float32)
    with rasterio.open(
        dem, "w", driver="GTiff", height=24, width=24, count=1, dtype="float32",
        crs="EPSG:32650", transform=from_origin(0, 0, 10, 10),
    ) as ds:
        ds.write(h, 1)
    field = HeightField.from_geotiff(str(dem), size=(1000.0, 1000.0), grid_n=12)
    assert np.allclose(field.passability, 1.0)
    f = field.move_factor(np.array([100.0, 500.0, 900.0]), np.array([100.0, 500.0, 900.0]))
    assert np.allclose(f, 1.0)


def test_battalion_scenario_resolves_passability_path():
    import os
    from pathlib import Path

    sc = YamlFileProvider().load_scenario("example_battalion")
    assert sc.terrain.passability_path is not None
    p = Path(sc.terrain.passability_path)
    assert p.name == "dem_pass.tif" and p.parent.name == "terrain"
    assert os.path.isabs(sc.terrain.passability_path)
    field = HeightField.from_spec(sc.terrain)
    assert field.passability.shape == (65, 65)
    assert float(field.passability.min()) >= 0.05
    assert float(field.passability.max()) <= 1.0
