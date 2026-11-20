"""批量诊断所有工况的 Mesh3D 索引合法性（不跑完整 visualize，直接调用各 log 函数）。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))

from brigsim.data.provider import YamlFileProvider
from brigsim.kernel.terrain import HeightField
from brigsim.viz import rerun_view as rv

PROVIDER = YamlFileProvider(str(REPO / "scenarios"))
SCENARIOS = ["example_company", "medium_brigade", "brigade_canyon_clear",
             "brigade_plain_fog", "brigade_rolling_fortified",
             "example_brigade", "example_battalion", "real_terrain_brigade"]


def check_one(name: str) -> list[str]:
    captured = []
    orig = rv.rr.log
    def spy(entity, data, **kw):
        if hasattr(data, "triangle_indices"):
            captured.append((entity, data))
        return  # 不真写
    rv.rr.log = spy
    issues = []
    try:
        sc = PROVIDER.load_scenario(name)
        hf = HeightField.from_spec(sc.terrain)
        # C7-j：用可视化统一后的真实 DEM（峡谷 + 太行山才会触发全遮挡）
        vis_hf = rv._visual_terrain(hf)
        zex = rv.auto_z_exag(vis_hf)
        geo = vis_hf.scaled(zex)
        info = rv._unit_info(sc)
        uids = list(info.keys())
        # 构造两个 tick 的假 snaps（所有单位存活 + 部分损毁）
        for tick in (0, 10):
            rows = []
            for i, u in enumerate(uids):
                alive = not (tick > 0 and i % 5 == 0)
                rows.append((3000 + (i % 8) * 800, 3000 + (i // 8) * 900,
                             0.3 + i * 0.01, alive, i % 7 == 0))
            cur = pd.DataFrame(rows, columns=["x", "y", "heading", "alive", "damaged"], index=uids)
            zs = geo.height_at(cur["x"].to_numpy(), cur["y"].to_numpy())
            rv._log_fans(cur, zs, info, outline=True, geo=geo, los_terrain=vis_hf)
            rv._log_fans(cur, zs, info, outline=False, geo=geo, los_terrain=vis_hf)
            rv._log_coverages(tick, cur, zs, info, thin=True, geo=geo, los_terrain=vis_hf)
        rv._log_terrain(geo, "day", zex)
    except Exception as e:
        issues.append(f"  EXCEPTION: {type(e).__name__}: {e}")
    finally:
        rv.rr.log = orig

    for entity, mesh in captured:
        try:
            tri = mesh.triangle_indices.as_arrow_array().to_pylist()
            pos = mesh.vertex_positions.as_arrow_array().to_pylist()
            nv = len(pos)
            flat = [int(v) for row in tri for v in (row if isinstance(row, list) else [row])]
            bad_rows = [r for r in tri if not isinstance(r, list) or len(r) != 3]
            # C7-j：空 triangle_indices 会使 viewer 报 "Mesh has no triangle indices"
            if len(tri) == 0:
                issues.append(f"  ❌ {entity}: 空 triangle_indices（viewer 会报错）")
            if bad_rows:
                issues.append(f"  ❌ {entity}: 非三元组行 {len(bad_rows)}")
            if flat and (max(flat) >= nv or min(flat) < 0):
                issues.append(f"  ❌ {entity}: 索引越界 max={max(flat)} nv={nv}")
        except Exception as e:
            issues.append(f"  ❌ {entity}: 检查异常 {e}")
    return issues


def main() -> None:
    total = 0
    for name in SCENARIOS:
        issues = check_one(name)
        total += len(issues)
        print(f"\n[{name}] {'✅ OK' if not issues else '❌ ' + str(len(issues)) + ' issues'}")
        for x in issues:
            print(x)
    print(f"\n总问题数: {total}")


if __name__ == "__main__":
    main()
