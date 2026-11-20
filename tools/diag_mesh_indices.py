"""诊断：拦截 visualize 产生的所有 Mesh3D，检查 triangle_indices 合法性。

rerun 0.39 要求 Mesh3D.triangle_indices 形状 (N,3)、整数 dtype、索引 < 顶点数。
本脚本跑一次真实想定的可视化回放，把每个 Mesh3D 的索引问题打印出来。

用法：
  PYTHONPATH=src .venv/Scripts/python.exe tools/diag_mesh_indices.py [scenario]
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))

from brigsim.data.provider import YamlFileProvider
from brigsim.dashboard.build import _make_kernel
from brigsim.experiment.runner import run_once
from brigsim.surrogate.design import apply_features
from brigsim.viz import rerun_view as rv

PROVIDER = YamlFileProvider(str(REPO / "scenarios"))


def _to_list(batch):
    """rerun 0.39 字段是 Arrow Batch，展开成 python list（可能是嵌套 list）。"""
    if batch is None:
        return None
    try:
        return batch.as_arrow_array().to_pylist()
    except Exception:
        return None


def _flatten_ints(x):
    out = []
    if isinstance(x, (list, tuple)):
        for v in x:
            out.extend(_flatten_ints(v))
    elif isinstance(x, (int, np.integer)):
        out.append(int(x))
    elif isinstance(x, (float, np.floating)) and float(x).is_integer():
        out.append(int(x))
    return out


def check_mesh(entity: str, mesh) -> list[str]:
    problems: list[str] = []
    tri = _to_list(getattr(mesh, "triangle_indices", None))
    pos = _to_list(getattr(mesh, "vertex_positions", None))
    col = _to_list(getattr(mesh, "vertex_colors", None))
    if tri is None:
        return problems
    nv = len(pos) if pos is not None else None
    flat = _flatten_ints(tri)
    # 每行应为 3 个 int
    row_lens = set()
    for row in tri:
        row_lens.add(len(row) if isinstance(row, (list, tuple)) else -1)
    problems.append(f"    rows={len(tri)} row_lens={row_lens} flat_n={len(flat)} nvtx={nv}")
    if row_lens - {3}:
        problems.append(f"    ❌ 有非三元组行: row_lens={row_lens}")
    if nv is not None and flat:
        mx, mn = max(flat), min(flat)
        if mx >= nv or mn < 0:
            problems.append(f"    ❌ 索引越界: min={mn} max={mx} nvtx={nv}")
    if col is not None and nv is not None and len(col) != nv:
        problems.append(f"    ❌ vertex_colors 数量不符: {len(col)} vs {nv}")
    return problems


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "example_brigade"
    captured: list[tuple[str, object]] = []
    orig_log = rv.rr.log

    def spy(entity, data, **kw):
        if hasattr(data, "triangle_indices"):
            captured.append((entity, data))
        return orig_log(entity, data, **kw)

    rv.rr.log = spy
    try:
        sc = PROVIDER.load_scenario(name)
        # knee 向量：用中庸参数（一半区间），足以触发所有图层
        from brigsim.surrogate.design import SPACE
        knee_x = np.asarray([0.5] * len(SPACE.names), dtype=float)
        sc2 = apply_features(sc, knee_x)
        factory, _ = _make_kernel("mesa", "cpu")
        _, kern = run_once(sc2, 20261014, factory)
        snap, eng = kern.log.to_frames()
        rv.visualize(
            snap, eng, scenario=sc2, terrain=kern.terrain,
            rrd_path=str(REPO / "tmp_rr" / f"_diag_{name}.rrd"),
            spawn=False, supply=kern.log.supply_frame(), air=kern.log.air_frame(),
            effects=kern.log.effect_frame(), app_id="diag",
            use_models=False, tick_stride=4, embed=True,
        )
    finally:
        rv.rr.log = orig_log

    print(f"\n=== {name}: 共 {len(captured)} 个 Mesh3D 日志记录 ===")
    bad = 0
    seen: dict[str, int] = {}
    for entity, mesh in captured:
        seen[entity] = seen.get(entity, 0) + 1
        probs = check_mesh(entity, mesh)
        if any("❌" in p for p in probs):
            bad += 1
            print(f"  ❌ {entity} (第{seen[entity]}次)")
            for p in probs:
                print(p)
    print(f"\n实体统计: {seen}")
    print(f"有问题实体数: {bad}")


if __name__ == "__main__":
    main()
