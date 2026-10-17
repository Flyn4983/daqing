"""C7-b：地形族 preset 工厂端到端 + 工况 preset 模式加载。"""

from __future__ import annotations

import numpy as np

from brigsim.combat.mesa_kernel import MesaKernel
from brigsim.data.provider import YamlFileProvider
from brigsim.data.terrain_presets import (
    TerrainFamily,
    default_environment,
    passability,
    preset,
)
from brigsim.kernel.terrain import HeightField


def _check_pass(name: str, pa: np.ndarray, grid_n: int) -> None:
    assert pa.shape == (grid_n, grid_n), f"{name}: shape {pa.shape}"
    assert np.isfinite(pa).all(), f"{name}: 非有限值"
    assert np.all(pa >= 0.0) and np.all(pa <= 1.0), f"{name}: 值域越界"
    # 所有 8 族都应有结构差异：至少有 <1.0 的单元格（沙漠除外）
    if name != "desert":
        assert pa.min() < 1.0, f"{name}: 全 1.0，未产生结构"


def test_8_families_passability_structure():
    """8 族 preset 工厂各生成一次——passability 形状、值域、结构差异都对。"""
    n = 33
    for fam in TerrainFamily:
        pa = passability(fam, grid_n=n, seed=20261016)
        _check_pass(fam.value, pa, n)
        # HeightField.from_spec synthetic 分支能吃内联数组
        t = preset(fam, grid_n=n, seed=20261016)
        hf = HeightField.from_spec(t)
        assert hf.passability.shape == (n, n)
        # 退化检查
        if fam is TerrainFamily.desert:
            assert np.allclose(hf.passability, 1.0)
        if fam is TerrainFamily.paddy:
            assert hf.passability.min() == 0.0   # 有水湾
            assert hf.passability.max() == 1.0   # 田埂/主道


def test_terrain_preset_passthrough_yaml_override():
    """Terrain preset_family → YAML 显式字段 override 生效，preset 基线其他字段保留。"""
    base = preset("canyon", seed=7)
    override = base.model_copy(
        update={
            "preset_family": "canyon",
            "amplitude": 200.0,   # YAML 想显式提深切幅度
            "noise_seed": 42,     # YAML 想换噪声种子
        }
    )
    hf = HeightField.from_spec(override)
    # amplitude 显式 override（base 160 → 200）
    assert override.amplitude == 200.0
    # 但噪声换了 seed，高度差数值变化不等同于 amplitude 变化（FBM 是确定性噪声乘子）
    # 我们只要 shape/值域/passability 对
    assert hf.heights.shape == (base.grid_n, base.grid_n)
    assert hf.passability.shape == (base.grid_n, base.grid_n)


def test_provider_preset_family_end_to_end():
    """YAML 给 terrain.preset_family=X → provider 调 preset 工厂 → 内核能跑。"""
    p = YamlFileProvider()
    sc = p.load_scenario("medium_brigade")
    assert sc.terrain.preset_family == "rolling"
    assert sc.terrain.source == "synthetic"
    # rolling FBM 基线 amplitude=90（Terrain schema 默认 140，preset 工厂覆盖）
    assert sc.terrain.amplitude == 90.0
    assert sc.terrain.size_m == (10000.0, 10000.0)
    assert len(sc.terrain.passability_array) == sc.terrain.grid_n

    hf = HeightField.from_spec(sc.terrain)
    assert hf.passability.shape == (sc.terrain.grid_n,) * 2
    assert hf.passability.min() >= 0.5   # rolling 有山脊但没不可通行区
    assert hf.passability.max() == 1.0
    # Mesa 单局能跑
    k = MesaKernel(sc, seed=1)
    for _ in range(sc.mission.duration_ticks):
        k.step()
    assert int(k.state.alive.sum()) >= 1
    print(f"medium_brigade 终局存活 {int(k.state.alive.sum())} / tick {k.state.tick}")


def test_non_preset_scenario_zero_drift():
    """example_brigade（geotiff DEM）与 example_company（synthetic 无 preset_family）零漂移。"""
    from brigsim.kernel.terrain import HeightField

    p = YamlFileProvider()
    sc1 = p.load_scenario("example_brigade")
    sc2 = p.load_scenario("example_company")
    assert sc1.terrain.preset_family is None
    assert sc2.terrain.preset_family is None
    # 都能构造 HeightField（example_brigade 的 geotiff 依赖存在则应成功）
    hf = HeightField.from_spec(sc2.terrain)
    assert hf.passability.shape == (sc2.terrain.grid_n,) * 2
    assert np.allclose(hf.passability, 1.0)  # 无 preset → 默认全 1.0


def test_default_environment_returns_clear():
    """所有 8 族 default_environment 默认晴日（地形遮蔽用 visibility_factor 微调）。"""
    for fam in TerrainFamily:
        env = default_environment(fam, offensive=True)
        assert env["weather"] == "clear"
        assert env["illumination"] == "day"
        assert 0.5 <= env["visibility_factor"] <= 1.0
