"""补齐各工况的 6 份前端 JSON，保证下拉切换时所有面板都有本工况数据。

- timeseries / distribution：该工况自己的仿真结果（未构建的按需生成）；
- sensitivity / pareto / surrogate / manifest：全局模型层产物，跨工况共用，
  从根 data/ 复制（与 tools/build_replays.py 的"复用基线模型"策略一致）。

用法：
  .venv/Scripts/python.exe tools/build_scenario_data.py
  .venv/Scripts/python.exe tools/build_scenario_data.py brigade_rolling_fortified
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

DATA_ROOT = REPO / "src" / "brigsim" / "agent" / "web" / "data"
SCEN_DIR = DATA_ROOT / "scenarios"
SCENARIOS = [
    "example_company", "medium_brigade", "brigade_canyon_clear",
    "brigade_plain_fog", "brigade_rolling_fortified",
    "example_brigade", "example_battalion", "real_terrain_brigade",
]
MODEL_FILES = ["sensitivity", "pareto", "surrogate"]
SIM_FILES = ["timeseries", "distribution"]


def copy_models() -> None:
    for name in SCENARIOS:
        out = SCEN_DIR / name
        out.mkdir(parents=True, exist_ok=True)
        for f in MODEL_FILES:
            src = DATA_ROOT / f"{f}.json"
            if src.is_file():
                shutil.copy2(src, out / f"{f}.json")


def seed_baseline() -> None:
    """example_brigade 的根 data/ 就是它的权威产物，直接复制，不重跑。"""
    out = SCEN_DIR / "example_brigade"
    out.mkdir(parents=True, exist_ok=True)
    for f in SIM_FILES + ["manifest"]:
        src = DATA_ROOT / f"{f}.json"
        if src.is_file() and not (out / f"{f}.json").is_file():
            shutil.copy2(src, out / f"{f}.json")


def build_missing(names: list[str]) -> None:
    from brigsim.dashboard.build import DEFAULT_PATHS, build_all, load_scenario

    for name in names:
        out = SCEN_DIR / name
        out.mkdir(parents=True, exist_ok=True)
        if all((out / f"{f}.json").is_file() for f in SIM_FILES):
            print(f"  {name:28s} 已有仿真数据，跳过", flush=True)
            continue
        sc = load_scenario(name)
        t0 = time.monotonic()
        print(f"  {name:28s} 构建 timeseries/distribution …", flush=True)
        build_all(
            out,
            scenario_name=name,
            duration_ticks=int(sc.mission.duration_ticks),
            paths=DEFAULT_PATHS,
            replay_path=None,
            only=set(SIM_FILES),
            make_rrd=False,
            n_workers=1,
        )
        print(f"  {name:28s} 完成 {time.monotonic() - t0:.1f}s", flush=True)


def fix_manifests() -> None:
    """补齐缺失的 manifest（brigade_plain_fog 等早期产物只有 3 个 JSON）。"""
    template = DATA_ROOT / "manifest.json"
    if not template.is_file():
        return
    base = json.loads(template.read_text(encoding="utf-8"))
    for name in SCENARIOS:
        out = SCEN_DIR / name / "manifest.json"
        if out.is_file():
            continue
        m = dict(base)
        m["scenario"] = name
        out.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    names = sys.argv[1:] or [n for n in SCENARIOS if n not in ("example_brigade",)]
    copy_models()
    seed_baseline()
    build_missing(names)
    fix_manifests()
    for name in SCENARIOS:
        out = SCEN_DIR / name
        have = [f for f in SIM_FILES + MODEL_FILES + ["manifest"] if (out / f"{f}.json").is_file()]
        print(f"{name:28s} {len(have)}/6  {','.join(have)}")


if __name__ == "__main__":
    main()
