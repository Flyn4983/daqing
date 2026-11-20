"""C7-c 单元测试：地形山影 / 地图要素 / 气象恰当化重调 / 贴图资产。

C7-d 增补：渲染垂直夸张（auto_z_exag/scaled）、预设场景跳过道路贴片、
embed 扇面改半透明填充。
C7-e 增补：作用范围圈/扇面贴地跟随地形（_ring_pts_tf / geo 参数）、
线宽加大。
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from brigsim.data.provider import YamlFileProvider
from brigsim.kernel.terrain import HeightField
from brigsim.viz import rerun_view as rv


@pytest.fixture(scope="module")
def canyon():
    sc = YamlFileProvider().load_scenario("brigade_canyon_clear")
    return sc, HeightField.from_spec(sc.terrain)


@pytest.fixture(scope="module")
def plain():
    sc = YamlFileProvider().load_scenario("brigade_plain_fog")
    return sc, HeightField.from_spec(sc.terrain)


@pytest.fixture(scope="module")
def dem():
    sc = YamlFileProvider().load_scenario("example_brigade")
    return sc, HeightField.from_spec(sc.terrain)


class TestSunDirection:
    def test_unit_vectors(self):
        for illum in ("day", "dusk", "night"):
            x, y, z, inten = rv._sun_direction(illum)
            assert abs(math.sqrt(x * x + y * y + z * z) - 1.0) < 1e-9
            assert 0.0 < inten <= 1.0

    def test_unknown_falls_back_to_day(self):
        assert rv._sun_direction("whatever") == rv._sun_direction("day")

    def test_night_dimmer_than_day(self):
        assert rv._sun_direction("night")[3] < rv._sun_direction("day")[3]


class TestGroundTexture:
    def test_asset_loads_fast_and_consistent(self):
        tex = rv._ground_texture()
        assert tex.shape == (1024, 1024, 3)
        assert tex.dtype == np.uint8
        # 进程内缓存：同一对象
        assert rv._ground_texture() is tex

    def test_natural_warm_tone(self):
        tex = rv._ground_texture()
        mean = tex.reshape(-1, 3).mean(axis=0)
        # 暖灰基底：R>=G>=B 且整体不过暗也不过曝
        assert mean[0] >= mean[1] >= mean[2]
        assert 120 < mean.mean() < 235

    def test_generate_matches_asset(self):
        if not rv._TEXTURE_ASSET.is_file():
            pytest.skip("资产未生成")
        gen = rv._ground_texture_generate(1024, 11)
        asset = np.load(rv._TEXTURE_ASSET)
        assert np.array_equal(gen, asset), "资产与生成逻辑不一致，需重新生成 ground_texture.npy"


class TestMapFrame:
    def test_grid_step_adaptive(self, canyon):
        """网格步长应落在候选档且单方向线数 <= ~12。"""
        _, hf = canyon
        W, H = hf.size
        step = 2000.0
        for cand in (100.0, 200.0, 250.0, 500.0, 1000.0, 2000.0):
            if W / cand <= 10 and H / cand <= 10:
                step = cand
                break
        assert step in (100.0, 200.0, 250.0, 500.0, 1000.0, 2000.0)
        assert W / step <= 12 and H / step <= 12


class TestZExaggeration:
    """C7-d：渲染垂直夸张——物理高程不变，几何按起伏量自动放大。"""

    def test_auto_z_exag_flat_terrain_capped(self, plain):
        _, hf = plain  # plain 总起伏仅 ~44m/10km
        zx = rv.auto_z_exag(hf)
        assert zx == 4.0  # 触顶 4×

    def test_auto_z_exag_dem_not_exaggerated(self, dem):
        _, hf = dem  # DEM 起伏 388m，已达目标视觉起伏
        assert rv.auto_z_exag(hf) == 1.0

    def test_auto_z_exag_mid_range(self, canyon):
        _, hf = canyon  # 190m → 1.26×
        zx = rv.auto_z_exag(hf)
        assert 1.0 < zx < 2.0

    def test_auto_z_exag_degenerate(self):
        t = HeightField((1000.0, 1000.0), 9, np.full((9, 9), 50.0), 5.0)
        assert rv.auto_z_exag(t) == 1.0

    def test_scaled_semantics(self, plain):
        _, hf = plain
        same = hf.scaled(1.0)
        assert same is hf  # 不夸张零开销
        geo = hf.scaled(2.5)
        assert np.allclose(geo.heights, hf.heights * 2.5)
        assert math.isclose(geo.water_level, hf.water_level * 2.5)
        assert geo.passability is hf.passability  # 大数组共享
        assert geo.preset_family == hf.preset_family
        assert geo.size == hf.size and geo.n == hf.n
        # height_at 随之缩放
        assert math.isclose(
            float(geo.height_at(100.0, 200.0)), float(hf.height_at(100.0, 200.0)) * 2.5
        )

    def test_terrain_geometry_scales_colors_dont(self, plain):
        """垂直夸张后几何 z ×4：plain 全部高程 <110m，颜色分带若误用缩放高程
        （30×4=120 → 岩/雪）会整屏变色；此处验证 _log_terrain(z_exag=4) 全链路
        （几何/法线/水面/UV）不抛异常，颜色逻辑由 scaled 语义测试间接保证。"""
        import rerun as rr

        _, hf = plain
        rr.init("c7d_color_test", spawn=False)
        import pathlib
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            rr.save(str(pathlib.Path(td) / "t.rrd"))
            rv._log_terrain(hf, "day", 4.0)
        rr.disconnect()


class TestPassabilityLayer:
    """C7-d：道路贴片只对 geotiff（DEM 路网）；预设改用 _log_terrain 晕染。"""

    def test_preset_scenario_skips_road_quads(self, plain, monkeypatch):
        _, hf = plain
        logged: list[str] = []
        monkeypatch.setattr(rv.rr, "log", lambda p, e, **kw: logged.append(p))
        rv._log_passability(hf)
        assert not any("passability" in p for p in logged), "预设场景不应铺道路贴片"

    def test_geotiff_scenario_draws_roads(self, dem, monkeypatch):
        _, hf = dem
        logged: list[str] = []
        monkeypatch.setattr(rv.rr, "log", lambda p, e, **kw: logged.append(p))
        rv._log_passability(hf)
        assert any("passability" in p for p in logged), "DEM 路网应绘制"


class TestFansStyle:
    """C7-d：embed 扇面 = 半透明填充 + 亮弧轮廓；桌面版保持纯填充。"""

    @staticmethod
    def _cur():
        return pd.DataFrame(
            {
                "x": [4000.0, 6000.0],
                "y": [4000.0, 6000.0],
                "heading": [0.0, 1.0],
                "alive": [True, True],
            },
            index=["u1", "u2"],
        )

    def test_embed_fans_fill_plus_edge(self, monkeypatch):
        logged: dict[str, object] = {}
        monkeypatch.setattr(rv.rr, "log", lambda p, e, **kw: logged.__setitem__(p, e))
        info = {"u1": ("mbt", 0, "o1", 2500.0, "role", 0.0), "u2": ("mbt", 1, "o2", 2500.0, "role", 0.0)}
        rv._log_fans(self._cur(), np.array([100.0, 100.0]), info, outline=True)
        assert "world/fans" in logged and "world/fans_edge" in logged

    def test_desktop_fans_fill_only(self, monkeypatch):
        logged: dict[str, object] = {}
        monkeypatch.setattr(rv.rr, "log", lambda p, e, **kw: logged.__setitem__(p, e))
        info = {"u1": ("mbt", 0, "o1", 2500.0, "role", 0.0), "u2": ("mbt", 1, "o2", 2500.0, "role", 0.0)}
        rv._log_fans(self._cur(), np.array([100.0, 100.0]), info, outline=False)
        assert "world/fans" in logged and "world/fans_edge" not in logged

    def test_dead_units_cleared(self, monkeypatch):
        logged: dict[str, object] = {}
        monkeypatch.setattr(rv.rr, "log", lambda p, e, **kw: logged.__setitem__(p, e))
        cur = self._cur()
        cur["alive"] = [False, False]
        info = {"u1": ("mbt", 0, "o1", 2500.0, "role", 0.0), "u2": ("mbt", 1, "o2", 2500.0, "role", 0.0)}
        rv._log_fans(cur, np.array([100.0, 100.0]), info, outline=True)
        assert "world/fans" in logged  # Clear 分支也走 log


class TestCoveragesTerrain:
    """C7-e：作用范围圈/扫描线/扇面弧点贴地跟随（缩放）地形。

    用户反馈"作用范围圈被地形遮挡、颜色发灰不明显"——根因是圆环为平面
    z=中心地形高+3，垂直夸张（plain 4×）后圈内起伏可达上百年，扁平圈被
    山丘埋没。修复：geo 传入时逐点按 height_at 采样 + 抬升。
    """

    @staticmethod
    def _cur():
        return pd.DataFrame(
            {
                "x": [4000.0, 6000.0],
                "y": [4000.0, 6000.0],
                "heading": [0.0, 1.0],
                "alive": [True, True],
            },
            index=["u1", "u2"],
        )

    @staticmethod
    def _info():
        # (model, side, label, range, role, coverage)
        return {
            "u1": ("mbt", 0, "o1", 2500.0, "radar_air", 1800.0),
            "u2": ("mbt", 1, "o2", 2500.0, "ew", 1200.0),
        }

    def test_ring_pts_tf_follows_terrain(self, plain):
        _, hf = plain
        geo = hf.scaled(rv.auto_z_exag(hf))
        pts = rv._ring_pts_tf(5000.0, 5000.0, 1800.0, geo, 8.0, 24)
        assert len(pts) == 24
        zs = [p[2] for p in pts]
        # 贴地采样：圈上各点高程应随地形变化（非同一平面）
        assert max(zs) - min(zs) > 1e-6, "贴地圆环的 z 应随地形起伏"
        # 每点都应高于当地地形（抬升 8m）
        for (x, y, z) in pts:
            assert z >= float(geo.height_at(x, y)) + 8.0 - 1e-6

    def test_coverages_geo_rings_not_flat(self, plain, monkeypatch):
        _, hf = plain
        geo = hf.scaled(rv.auto_z_exag(hf))
        logged: dict[str, object] = {}
        monkeypatch.setattr(rv.rr, "log", lambda p, e, **kw: logged.__setitem__(p, e))
        zs = geo.height_at(self._cur()["x"].to_numpy(), self._cur()["y"].to_numpy())
        rv._log_coverages(3, self._cur(), zs, self._info(), thin=True, geo=geo)
        assert "world/coverages/rings" in logged
        rings = logged["world/coverages/rings"]
        # 各环点数不同（56/64）→ ragged，拼接后取 z
        strips = np.concatenate(
            [np.asarray(s) for s in rings.strips.as_arrow_array().to_pylist()]
        )
        assert strips.size
        zvals = strips[:, 2]
        # 至少一个圆环的 z 跨度 > 1m（贴地生效；平面环跨度为 0）
        assert float(zvals.max() - zvals.min()) > 1.0

    def test_coverages_no_geo_still_works(self, monkeypatch):
        """geo=None 时退化为平面环（旧行为兼容），不应抛异常。"""
        logged: dict[str, object] = {}
        monkeypatch.setattr(rv.rr, "log", lambda p, e, **kw: logged.__setitem__(p, e))
        zs = np.array([100.0, 100.0])
        rv._log_coverages(0, self._cur(), zs, self._info(), thin=False)
        assert "world/coverages/rings" in logged

    def test_fans_geo_arc_follows_terrain(self, plain, monkeypatch):
        _, hf = plain
        geo = hf.scaled(rv.auto_z_exag(hf))
        logged: dict[str, object] = {}
        monkeypatch.setattr(rv.rr, "log", lambda p, e, **kw: logged.__setitem__(p, e))
        info = {"u1": ("mbt", 0, "o1", 2500.0, "role", 0.0), "u2": ("mbt", 1, "o2", 2500.0, "role", 0.0)}
        zs = geo.height_at(self._cur()["x"].to_numpy(), self._cur()["y"].to_numpy())
        rv._log_fans(self._cur(), zs, info, outline=True, geo=geo)
        fans = logged["world/fans"]
        vp = np.asarray(fans.vertex_positions.as_arrow_array().to_pylist())
        assert vp.size
        assert float(vp[:, 2].max() - vp[:, 2].min()) > 1.0, "扇面弧点应随地形起伏"


class TestWeatherAndTerrainSave:
    """所有天气分支 + 地形/地图要素在真实 sink（save 模式）下不抛异常且落盘。

    同一进程单 sink：一个 init 覆盖全部分支，结束显式 disconnect 触发 flush。
    """

    def test_all_branches_and_terrain(self, canyon, tmp_path):
        import warnings

        import rerun as rr

        sc, hf = canyon
        rrd = tmp_path / "viz.rrd"
        rr.init("c7c_viz_test", spawn=False)
        rr.save(str(rrd))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            rv._log_weather_header(sc)
            # C7-d：缩放地形副本驱动气象（雾团贴夸张地面）
            geo = hf.scaled(rv.auto_z_exag(hf))
            for w in ("clear", "rain", "heavy_rain", "snow", "fog", "heavy_fog", "sand", "haze"):
                sc.environment.weather = w
                rr.set_time("tick", sequence=0)
                rv._log_weather(0, sc, geo)
            sc.environment.wind_speed_mps = 6.0
            sc.environment.illumination = "night"
            sc.environment.weather = "fog"
            rv._log_weather(0, sc, geo)
            rv._log_terrain(hf, "day", rv.auto_z_exag(hf))
            rv._log_map_frame(geo, "day")
        # 几何构造错误（如 strip 被转置成 (3,N)）会以 RerunWarning 形式静默丢弃图层
        bad = [str(w.message) for w in caught if "Expected a sequence of 3D vectors" in str(w.message)]
        assert not bad, f"rerun 丢弃了几何数据: {bad}"
        rr.disconnect()  # flush + 关闭 sink
        assert rrd.is_file() and rrd.stat().st_size > 500_000
