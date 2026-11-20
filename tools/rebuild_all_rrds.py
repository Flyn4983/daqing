"""重建全部工况 3D 回放 rrd，并校验真实空中模型是否写入。

C7-k 根因：8 个工况里有 6 个 rrd 是在最终空中模型代码完成前构建的，
前端又用同一文件名直载（浏览器/代理缓存旧文件），表现为"只有某个工况
的地形和装备是对的"。本脚本在每次可视化代码变更后统一重建 + 校验。

用法：
  .venv/Scripts/python.exe tools/rebuild_all_rrds.py
  .venv/Scripts/python.exe tools/rebuild_all_rrds.py --only example_brigade
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

SCENARIOS = [
    "example_company",
    "medium_brigade",
    "brigade_canyon_clear",
    "brigade_plain_fog",
    "brigade_rolling_fortified",
    "example_brigade",
    "example_battalion",
    "real_terrain_brigade",
]

AIR_ENTITY_RE = re.compile(rb"world/air/[A-Za-z0-9_-]+/model")


def air_package_count(scenario: str) -> int:
    from brigsim.data.provider import YamlFileProvider

    sc = YamlFileProvider(str(REPO / "scenarios")).load_scenario(scenario)
    return len(getattr(sc, "air_packages", []) or [])


def model_entities(path: Path) -> int:
    data = path.read_bytes()
    return len(set(AIR_ENTITY_RE.findall(data)))


def rebuild_one(name: str, *, tick_stride: int = 2) -> tuple[bool, str]:
    out = REPO / "outputs" / "dash" / f"replay_{name}.rrd"
    cmd = [
        sys.executable, "-m", "brigsim.cli", "dash-replay", name,
        "--out", str(out), "--tick-stride", str(tick_stride),
    ]
    t0 = time.monotonic()
    proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True, timeout=1800)
    if proc.returncode != 0 or not out.is_file():
        return False, (proc.stderr or proc.stdout or "未知错误")[-500:]
    expected = air_package_count(name)
    got = model_entities(out)
    size_mb = out.stat().st_size / 1024 / 1024
    ok = got == expected
    msg = (f"{name:28s} {size_mb:7.1f}MB  空中模型 {got}/{expected}  "
           f"{time.monotonic() - t0:5.1f}s  {'OK' if ok else 'MISMATCH'}")
    return ok, msg


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", nargs="*", help="只重建指定工况")
    ap.add_argument("--tick-stride", type=int, default=2)
    args = ap.parse_args()

    names = args.only or SCENARIOS
    failures = []
    for n in names:
        ok, msg = rebuild_one(n, tick_stride=args.tick_stride)
        print(msg, flush=True)
        if not ok:
            failures.append((n, msg))
    if failures:
        print("\n失败工况：")
        for n, msg in failures:
            print(" ", n, msg)
        sys.exit(1)
    print(f"\n全部 {len(names)} 个工况 rrd 重建并校验通过。")


if __name__ == "__main__":
    main()
