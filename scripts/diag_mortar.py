"""探针：营属迫击炮为何不发射（tracks/冷却/座板状态）。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from brigsim.combat.mesa_kernel import MesaKernel  # noqa: E402
from brigsim.data.provider import YamlFileProvider  # noqa: E402

sc = YamlFileProvider().load_scenario("example_brigade")
k = MesaKernel(sc, seed=100)
s = k.state
mor = [i for i in range(s.n) if str(s.unit_ids[i]).startswith("rmor")]
print("迫击炮阵位:", [(str(s.unit_ids[i]), float(s.x[i]), float(s.y[i])) for i in mor])
probe_ticks = {14, 16, 18}
terr = k.terrain
sysm = k.sys
while not k.finished:
    k.step()
    t = int(s.tick)
    if t in probe_ticks:
        # 红方地面单位对蓝方一线（ba/bb/bc, idx by id）的探测机会分解
        line = [j for j in range(s.n) if str(s.unit_ids[j])[:2] in ("ba", "bb", "bc")]
        n_reach = n_los = n_smoke = 0
        reds = np.where(s.alive & (s.side == 0))[0]
        for i in reds:
            kind = str(s.sensor_kind[i])
            reach = s.sensor_range[i] * sysm.factors[kind]
            for j in line:
                if not s.alive[j]:
                    continue
                d = float(np.hypot(s.x[j]-s.x[i], s.y[j]-s.y[i]))
                if d > reach:
                    continue
                n_reach += 1
                if not terr.has_los(float(s.x[i]), float(s.y[i]),
                                   float(s.x[j]), float(s.y[j])):
                    continue
                n_los += 1
                if kind in ("optical", "ir", "fused") and (
                        sysm.in_smoke(float(s.x[i]), float(s.y[i]))
                        or sysm.in_smoke(float(s.x[j]), float(s.y[j]))):
                    n_smoke += 1
        # 地形高程剖面：从 (4400,5000) 到 (6000,5000)
        xs = np.linspace(4400, 6000, 9)
        prof = [float(terr.height_at(float(x), 5000.0)) for x in xs]
        print(f"\nt={t} 红-一线: 射程内对数={n_reach} 有通视={n_los} 其中烟幕遮={n_smoke}")
        print("  地形剖面 y=5000 x=4400..6000: "
              + " ".join(f"{h:.1f}" for h in prof))
        tr = k.tracks[0]
        print(f"  共享航迹 {len(tr)} 条: "
              + ", ".join(f"{s.unit_ids[j]}({info['src']})" for j, info in list(tr.items())[:12]))
