"""批量重建大屏所有工况的 replay rrd（C7-i：地形统一太行山真实 DEM + 索引修复）。

逐个调用 dash-replay 子进程（避免同进程 rerun sink 冲突），输出到
outputs/dash/replay_<scenario>.rrd —— 大屏 /api/scene-switch 直接加载此路径。

用法:
    PYTHONPATH=src .venv/Scripts/python.exe tools/rebuild_all_rrd.py [scenario...]
    PYTHONPATH=src .venv/Scripts/python.exe tools/rebuild_all_rrd.py   # 全部
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCENARIOS = [
    "example_company", "medium_brigade", "brigade_canyon_clear",
    "brigade_plain_fog", "brigade_rolling_fortified", "example_brigade",
    "example_battalion", "real_terrain_brigade",
]


def main() -> None:
    want = sys.argv[1:] or SCENARIOS
    out_dir = REPO / "outputs" / "dash"
    out_dir.mkdir(parents=True, exist_ok=True)
    py = REPO / ".venv" / "Scripts" / "python.exe"
    print(f"重建 {len(want)} 个 rrd → {out_dir}", flush=True)
    t_all = time.perf_counter()
    for name in want:
        out = out_dir / f"replay_{name}.rrd"
        print(f"\n[{name}] → {out.name}", flush=True)
        t0 = time.perf_counter()
        cmd = [str(py), "-m", "brigsim.cli", "dash-replay", name, "--out", str(out)]
        r = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True,
                           env={**__import__("os").environ, "PYTHONPATH": "src", "BASH_ENV": ""})
        dt = time.perf_counter() - t0
        if r.returncode == 0 and out.is_file():
            print(f"  ✅ {dt:.1f}s  {out.stat().st_size / 1e6:.1f}MB", flush=True)
        else:
            print(f"  ❌ rc={r.returncode} {dt:.1f}s", flush=True)
            print("  stdout:", r.stdout[-500:], flush=True)
            print("  stderr:", r.stderr[-500:], flush=True)
    print(f"\n总耗时 {time.perf_counter() - t_all:.1f}s", flush=True)


if __name__ == "__main__":
    main()
