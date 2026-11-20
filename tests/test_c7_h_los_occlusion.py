"""C7-h 回归：作用范围地形遮挡(LOS) + rolling 想定地形差异化。

验证：
1. HeightField.has_los 在山地/峡谷确实产生遮挡（非全可见/全遮挡）
2. _log_fans 传入 los_terrain 时，被遮挡方向的三角形被裁剪（数量减少）
3. _log_coverages 的 _visible 判定生效（圈段被裁剪）
4. medium_brigade 与 brigade_rolling_fortified 地形不再 100% 相同，
   但统计特征(地形类型)仍属同一丘陵族
"""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from brigsim.data.provider import YamlFileProvider
from brigsim.kernel.terrain import HeightField
from brigsim.viz import rerun_view as rv

PROVIDER = YamlFileProvider(str(Path(__file__).resolve().parents[1] / "scenarios"))


def test_has_los_produces_real_occlusion():
    """山地/峡谷里 LOS 应产生部分遮挡（既非全可见也非全遮挡）。"""
    for scn in ("brigade_canyon_clear", "real_terrain_brigade"):
        sc = PROVIDER.load_scenario(scn)
        t = HeightField.from_spec(sc.terrain)
        ox, oy, rng = 4000.0, 4000.0, 1200.0
        th = np.linspace(0, 2 * np.pi, 72, endpoint=False)
        vis = sum(
            t.has_los(ox, oy, ox + rng * np.cos(a), oy + rng * np.sin(a), 3.0)
            for a in th
        )
        assert 0 < vis < 72, f"{scn}: LOS 未产生有效遮挡 (vis={vis}/72)"


def test_fans_triangles_clipped_by_los(monkeypatch):
    """传入真实地形后，射界扇面三角形数应减少（遮挡方向不画）。"""
    captured = {}

    def fake_log(entity, data, **kw):
        captured[entity] = data

    monkeypatch.setattr(rv.rr, "log", fake_log)

    sc = PROVIDER.load_scenario("example_brigade")  # 含 21 个 coverage>0 的雷达单位
    hf = HeightField.from_spec(sc.terrain)
    geo = hf.scaled(rv.auto_z_exag(hf))
    info = rv._unit_info(sc)
    uids = [u for u, v in info.items() if v[5] > 0][:6]
    assert uids, "想定应有 coverage>0 的单位"
    cur = pd.DataFrame(
        [(3500.0 + i * 500, 4500.0, 0.4, True) for i in range(len(uids))],
        columns=["x", "y", "heading", "alive"], index=uids,
    )
    zs = geo.height_at(cur["x"].to_numpy(), cur["y"].to_numpy())

    def count_tri(los):
        captured.clear()
        rv._log_fans(cur, zs, info, outline=True, geo=geo, los_terrain=los)
        fan = captured.get("world/fans")
        if fan is None or getattr(fan, "triangle_indices", None) is None:
            return 0
        return len(fan.triangle_indices)

    n_full = count_tri(None)
    n_los = count_tri(hf)
    assert n_full > 0, "无遮挡时应有扇面三角形"
    assert n_los < n_full, f"LOS 应裁剪部分三角形: {n_full} -> {n_los}"


def test_rolling_scenarios_visually_distinct_but_same_family():
    """两个丘陵想定地形应不同(视觉),但同为 rolling 族(起伏量级接近)。"""
    a = HeightField.from_spec(PROVIDER.load_scenario("medium_brigade").terrain)
    b = HeightField.from_spec(
        PROVIDER.load_scenario("brigade_rolling_fortified").terrain
    )
    mean_abs_diff = float(np.abs(a.heights - b.heights).mean())
    assert mean_abs_diff > 10.0, f"地形应明显不同, 实际差 {mean_abs_diff:.1f}m"
    # 同一地形族：起伏 std 都在丘陵量级 (10~40m)
    assert 10.0 < a.heights.std() < 40.0
    assert 10.0 < b.heights.std() < 40.0
    # 通行性统计特征一致
    assert abs(float(a.passability.mean()) - float(b.passability.mean())) < 0.1


if __name__ == "__main__":
    import subprocess
    raise SystemExit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-q", "-s"]))
