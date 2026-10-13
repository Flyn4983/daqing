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
    assert float(f.min()) >= 0.1 and float(f.max()) <= 1.0
