"""C6 大屏数据构建器：C1–C5 产物 → 6 个前端 JSON（+ 1 个本地 rrd 回放）。

纯转换四件套（sensitivity/surrogate/pareto/manifest）只读 CSV/parquet/joblib；
仿真两件套（timeseries/distribution）与 rrd 回放见后续分段（同文件）。

约定：
- 所有 JSON ``json.dumps(allow_nan=False)``，非有限值一律转 None（前端显式判空）；
- schema 名 ``brigsim-dash/<name>/1``；
- 可选原始资产缺失时跳过对应构建并返回跳过原因，绝不让全量构建整体失败。
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..surrogate.design import FEATURE_NAMES, TARGETS
from .labels import (
    KNOB_LABELS,
    METRIC_LABELS,
    PARAM_LABELS,
    PLAN_LABELS,
    PLAN_SCENARIO_LABELS,
    SCHEMA_PREFIX,
    SCHEMA_VERSION,
    SENS_METRIC_LABELS,
    TICK_SECONDS,
)

# v3 标定基线保障方案（与 C4 nsga.evaluate_baseline、C5 工具基线同源）
BASELINE_X = [6.0, 4.0, 12.0, 8.0, 20.0, 8.0, 1.0, 1.0, 1.0, 1.0]
# knee 推荐方案 10 维向量（前 4 维决策来自 models/pareto_brigade_v1.csv rank_tag=knee 行，
# 后 6 维为固定敌情/情景；与 C5 tools 中 knee 方案一致）
KNEE_X = [3.0, 4.0, 30.0, 8.0, 40.0, 2.0, 1.0, 1.0, 1.0, 1.0]
# C3 DEVLOG 实测口径：批量推演约 123 ms/复制；代理端到端 0.003 ms/方案（约 4.1 万倍）。
# 大屏标题口径统一锚定 C3 已记录的端到端数（跨交付物/答辩一致）；活体纯推理微基准
# 随硬件与批量在亚微秒级漂移（实测 0.0007–0.003 ms），只作副字段参考，不作标题数。
FALLBACK_SIM_SECONDS_PER_POINT = 0.123
C3_E2E_PREDICT_MS_PER_POINT = 0.003

JSON_FILES = (
    "timeseries.json",
    "distribution.json",
    "sensitivity.json",
    "surrogate.json",
    "pareto.json",
    "manifest.json",
)


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------


def schema(name: str) -> str:
    return f"{SCHEMA_PREFIX}/{name}/{SCHEMA_VERSION}"


def r4(x: Any, ndigits: int = 4) -> float | None:
    """转四舍五入 float；非有限值（NaN/Inf）→ None（JSON null，前端判空）。"""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return round(f, ndigits)


def write_json(obj: dict, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(obj, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    path.write_text(body, encoding="utf-8")
    return path


def _load_model(model_path: str | Path):
    from ..surrogate.model import SurrogateModel

    return SurrogateModel.load(str(model_path))


# ---------------------------------------------------------------------------
# 灵敏度（Sobol S1/ST）
# ---------------------------------------------------------------------------


def build_sensitivity(csv_path: str | Path, out_path: str | Path) -> Path:
    df = pd.read_csv(csv_path)
    metrics = []
    for key in df["metric"].drop_duplicates():
        sub = df[df["metric"] == key].sort_values("rank")
        rows = [
            {
                "param": str(r.param),
                "label": PARAM_LABELS.get(str(r.param), str(r.param)),
                "S1": r4(r.S1),
                "S1_conf": r4(r.S1_conf),
                "ST": r4(r.ST),
                "ST_conf": r4(r.ST_conf),
                "rank": int(r.rank),
            }
            for r in sub.itertuples()
        ]
        metrics.append(
            {
                "key": str(key),
                "label": SENS_METRIC_LABELS.get(str(key), str(key)),
                "rows": rows,
                "top3": [
                    {"param": row["param"], "label": row["label"], "ST": row["ST"]}
                    for row in rows[:3]
                ],
            }
        )
    return write_json(
        {
            "schema": schema("sensitivity"),
            "method": "Sobol 方差分解（N=128×8 复制，v3 标定基线）",
            "metrics": metrics,
        },
        out_path,
    )


# ---------------------------------------------------------------------------
# 代理模型（精度 + parity + 加速比）
# ---------------------------------------------------------------------------


def build_surrogate(
    parquet_path: str | Path, model_path: str | Path, out_path: str | Path
) -> Path:
    from sklearn.metrics import mean_absolute_error, r2_score

    df = pd.read_parquet(parquet_path)
    holdout = df[df["split"] == "holdout"] if "split" in df.columns else df
    model = _load_model(model_path)
    pred = model.predict(holdout[FEATURE_NAMES].to_numpy(float))

    targets = []
    for j, name in enumerate(TARGETS):
        yt = holdout[name].to_numpy(float)
        yp = pred[name].to_numpy(float)
        mask = np.isfinite(yt) & np.isfinite(yp)
        label, kind = METRIC_LABELS[name]
        targets.append(
            {
                "key": name,
                "label": label,
                "kind": kind,
                "r2": r4(r2_score(yt[mask], yp[mask]), 4) if mask.sum() >= 2 else None,
                "mae": r4(mean_absolute_error(yt[mask], yp[mask]), 4)
                if mask.sum() >= 1
                else None,
                "points": [
                    [r4(yp[i], 4), r4(yt[i], 4)]
                    for i in range(len(yt))
                    if mask[i]
                ],
            }
        )

    # 加速比标题口径：C3 DEVLOG 端到端实测（0.003 ms/方案），保证跨交付物一致；
    # 活体纯推理微基准仅记录为副字段（随硬件/批量漂移，不用于标题）。
    bench_s, n_bench = model.predict_benchmark(20_000)
    live_pred_ms = bench_s / n_bench * 1000.0
    md = model.metadata
    sim_per_point = None
    timing_source = "C3 DEVLOG 实测常量（GPU 批量约 123 ms/推演）"
    if md is not None and md.sim_wall_seconds:
        n_reps = md.n_train * md.reps_train + md.n_holdout * md.reps_holdout
        if n_reps > 0:
            sim_per_point = md.sim_wall_seconds / n_reps
            timing_source = "训练集生成墙钟 / 推演次数（现场计算）"
    sim_per_point = sim_per_point or FALLBACK_SIM_SECONDS_PER_POINT
    speedup = sim_per_point / (C3_E2E_PREDICT_MS_PER_POINT / 1000.0)
    live_speedup = sim_per_point / (live_pred_ms / 1000.0)

    n_train = int((df["split"] == "train").sum()) if "split" in df.columns else 0
    n_holdout = int((df["split"] == "holdout").sum()) if "split" in df.columns else len(df)
    return write_json(
        {
            "schema": schema("surrogate"),
            "model": "MLP(64,64) 标准化多输出代理（scikit-learn）",
            "n_train": n_train,
            "n_holdout": n_holdout,
            "targets": targets,
            "benchmark": {
                "predict_ms_per_point": r4(C3_E2E_PREDICT_MS_PER_POINT, 6),
                "sim_seconds_per_point": r4(sim_per_point, 5),
                "speedup": r4(speedup, 1),
                "source": timing_source + "；标题预测耗时锚定 C3 端到端 0.003 ms/方案",
                "live_predict_ms_per_point": r4(live_pred_ms, 6),
                "live_speedup": r4(live_speedup, 1),
                "n_bench_points": int(n_bench),
                "live_note": "本机 2 万方案纯推理活体微基准（随硬件/批量漂移，仅参考）",
            },
        },
        out_path,
    )


# ---------------------------------------------------------------------------
# Pareto 方案表 + 基线 + 复验
# ---------------------------------------------------------------------------

_DROP_PARETO_COLS = {"f_success_neg", "f_attrition_red", "f_cost"}


def build_pareto(
    pareto_csv: str | Path,
    validation_csv: str | Path,
    model_path: str | Path,
    out_path: str | Path,
    duration_ticks: int = 59,
) -> Path:
    from ..optimize.nsga import evaluate_baseline
    from ..optimize.problem import ScenarioContext

    df = pd.read_csv(pareto_csv)
    plans = []
    tags: dict[str, int] = {}
    for i, r in enumerate(df.itertuples()):
        row = {
            k: (int(getattr(r, k)) if k in KNOB_LABELS else r4(getattr(r, k)))
            for k in df.columns
            if k not in _DROP_PARETO_COLS
        }
        plans.append(row)
        tag = str(getattr(r, "rank_tag", "") or "").strip()
        if tag in PLAN_LABELS:
            tags[tag] = i

    model = _load_model(model_path)
    base = evaluate_baseline(model, duration_ticks, ScenarioContext.baseline())
    baseline = {
        k: (int(base[k]) if k in KNOB_LABELS else r4(base[k]))
        for k in base.index
        if k not in _DROP_PARETO_COLS
    }

    val = pd.read_csv(validation_csv)
    validation = [
        {
            "plan": str(r.plan),
            "metric": str(r.metric),
            "metric_label": METRIC_LABELS.get(str(r.metric), (str(r.metric), ""))[0],
            "surrogate": r4(r.surrogate),
            "simulation": r4(r.simulation),
            "abs_error": r4(r.abs_error),
            "sim_std": r4(r.sim_std),
        }
        for r in val.itertuples()
    ]

    return write_json(
        {
            "schema": schema("pareto"),
            "duration_ticks": duration_ticks,
            "n_plans": len(plans),
            "plans": plans,
            "baseline": baseline,
            "tags": tags,
            "tag_labels": PLAN_LABELS,
            "knob_labels": KNOB_LABELS,
            "validation": validation,
        },
        out_path,
    )


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------


def build_manifest(
    out_dir: str | Path,
    *,
    scenario: str,
    duration_ticks: int,
    vectors: dict[str, list[float]],
    replay_path: str | Path | None = None,
) -> Path:
    out_dir = Path(out_dir)
    files = {}
    for name in JSON_FILES:
        p = out_dir / name
        if p.is_file() and name != "manifest.json":
            files[name] = p.stat().st_size
    replay = {"ready": False, "path": None}
    if replay_path is not None and Path(replay_path).is_file():
        replay = {"ready": True, "path": str(replay_path)}
    return write_json(
        {
            "schema": schema("manifest"),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "scenario": scenario,
            "duration_ticks": int(duration_ticks),
            "tick_seconds": TICK_SECONDS,
            "vectors": vectors,
            "files": files,
            "replay": replay,
        },
        out_dir / "manifest.json",
    )


# ---------------------------------------------------------------------------
# 全量编排
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PATHS = {
    "sensitivity": REPO_ROOT / "outputs" / "sensitivity_sobol_n128r8_v3baseline.csv",
    "pareto": REPO_ROOT / "models" / "pareto_brigade_v1.csv",
    "validation": REPO_ROOT / "models" / "pareto_brigade_v1_validation.csv",
    "parquet": REPO_ROOT / "outputs" / "datasets" / "surrogate_brigade_v1.parquet",
    "model": REPO_ROOT / "models" / "surrogate_brigade_v1.joblib",
}
DEFAULT_RRD = REPO_ROOT / "outputs" / "dash" / "replay_knee.rrd"


def build_all(
    out_dir: str | Path,
    *,
    scenario_name: str = "example_brigade",
    duration_ticks: int = 59,
    paths: dict[str, str | Path] | None = None,
    vectors: dict[str, list[float]] | None = None,
    replay_path: str | Path | None = DEFAULT_RRD,
    only: set[str] | list[str] | tuple[str, ...] | None = None,
    ts_reps: int = 8,
    dist_reps: int = 200,
    batch_size: int = 256,
    seed: int = 20261014,
    kernel: str = "warp",
    device: str = "cuda:0",
    make_rrd: bool = True,
) -> dict[str, Any]:
    """构建全部大屏数据。返回 {产物名: Path 或 ("skipped", 原因)}。

    timeseries/distribution/replay 三项需要仿真（见同文件后续分段实现）；
    only 给名字集合时只构建子集（manifest 始终构建）。
    """
    p = dict(DEFAULT_PATHS)
    if paths:
        p.update({k: Path(v) for k, v in paths.items()})
    out_dir = Path(out_dir)
    wanted = {s.strip() for s in only} if only else {
        "timeseries", "distribution", "sensitivity", "surrogate", "pareto", "replay"
    }
    results: dict[str, Any] = {}

    def _skip(name: str, why: str) -> None:
        results[name] = ("skipped", why)

    if "sensitivity" in wanted:
        if Path(p["sensitivity"]).is_file():
            results["sensitivity"] = build_sensitivity(
                p["sensitivity"], out_dir / "sensitivity.json"
            )
        else:
            _skip("sensitivity", f"缺少 {p['sensitivity']}")

    if "surrogate" in wanted:
        if Path(p["parquet"]).is_file() and Path(p["model"]).is_file():
            results["surrogate"] = build_surrogate(
                p["parquet"], p["model"], out_dir / "surrogate.json"
            )
        else:
            _skip("surrogate", "缺少代理数据集 parquet 或模型 joblib")

    if "pareto" in wanted:
        if all(Path(p[k]).is_file() for k in ("pareto", "validation", "model")):
            results["pareto"] = build_pareto(
                p["pareto"], p["validation"], p["model"],
                out_dir / "pareto.json", duration_ticks,
            )
        else:
            _skip("pareto", "缺少 Pareto/validation CSV 或模型 joblib")

    if "timeseries" in wanted or "distribution" in wanted or "replay" in wanted:
        _build_sim_outputs(
            wanted=wanted,
            results=results,
            out_dir=out_dir,
            scenario_name=scenario_name,
            duration_ticks=duration_ticks,
            seed=seed,
            ts_reps=ts_reps,
            dist_reps=dist_reps,
            batch_size=batch_size,
            kernel=kernel,
            device=device,
            make_rrd=make_rrd and "replay" in wanted,
            rrd_out=replay_path if replay_path is not None else DEFAULT_RRD,
        )

    if vectors is None:
        vectors = {"knee": KNEE_X, "baseline": BASELINE_X}
    results["manifest"] = build_manifest(
        out_dir,
        scenario=scenario_name,
        duration_ticks=duration_ticks,
        vectors=vectors,
        replay_path=replay_path,
    )
    return results


# ---------------------------------------------------------------------------
# 仿真构建器：四率时序 / 蒙特卡洛分布（3D 回放 rrd 见 build_replay）
# ---------------------------------------------------------------------------

TS_METRICS = ("availability", "attrition", "repair_rate", "failure_rate")
# 四率在 t=0 的物理初值（用于早停复制 reindex 后仍缺失的首部位置）
_TS_INIT = {"availability": 1.0, "attrition": 0.0, "repair_rate": 1.0, "failure_rate": 0.0}
_SIDE_NAMES = {0: "red", 1: "blue"}


def load_scenario(name_or_path: str):
    from ..data.provider import YamlFileProvider

    return YamlFileProvider().load_scenario(name_or_path)


def _pick_warp_device(device: str) -> str | None:
    """探测可用 Warp 设备：优先指定（如 cuda:0），回退 CPU；都失败返回 None。"""
    try:
        import warp as wp
    except ImportError:
        return None
    for cand in (device, "cpu"):
        try:
            wp.get_device(cand)
            return cand
        except Exception:  # noqa: BLE001 - 设备不可用就继续降级
            continue
    return None


def _make_kernel(kernel: str, device: str):
    """返回 (单环境 factory, 批量设备或 None)；warp 不可用时回退 Mesa。"""
    if kernel == "mesa":
        from ..combat.mesa_kernel import MesaKernel

        return MesaKernel, None
    dev = _pick_warp_device(device)
    if dev is not None:
        from ..combat.warp_kernel import WarpKernel

        return (lambda sc, seed=0: WarpKernel(sc, seed, device=dev)), dev
    from ..combat.mesa_kernel import MesaKernel

    return MesaKernel, None


def _run_replicate_rows(scenario, reps, seed, kernel, device, batch_size):
    """蒙特卡洛复制：warp 批量优先，否则 Mesa 顺序。返回指标行列表。"""
    factory, batch_device = _make_kernel(kernel, device)
    if batch_device is not None:
        from ..combat.warp_batch import run_batch

        rows = []
        for start in range(0, reps, batch_size):
            count = min(batch_size, reps - start)
            seeds = [seed + start + r for r in range(count)]
            rows.extend(run_batch(scenario, seeds, device=batch_device))
        return rows
    from ..experiment.runner import run_once

    return [run_once(scenario, seed + r, factory)[0] for r in range(reps)]


def build_timeseries(
    out_path: str | Path,
    scenario,
    plan_vectors: dict[str, list[float]],
    reps: int = 8,
    seed: int = 20261014,
    kernel: str = "warp",
    device: str = "cuda:0",
) -> Path:
    """两方案（knee/基线）各 reps 次单环境复制，聚合逐 tick 四率 mean/std。"""
    from ..experiment.four_rates import four_rates_timeseries
    from ..experiment.runner import run_once
    from ..surrogate.design import apply_features

    duration = int(scenario.mission.duration_ticks)
    factory, _ = _make_kernel(kernel, device)
    ticks = list(range(duration))
    collected: dict[tuple[str, int], list[pd.DataFrame]] = {}

    for key, x in plan_vectors.items():
        sc = apply_features(scenario, np.asarray(x, dtype=float))
        for r in range(reps):
            _, kernel_obj = run_once(sc, seed + r, factory)
            snap, _ = kernel_obj.log.to_frames()
            ts = four_rates_timeseries(
                kernel_obj.state, snap, kernel_obj.sustain.event_frame()
            )
            for side in (0, 1):
                sub = (
                    ts[ts["side"] == side]
                    .set_index("tick")
                    .reindex(ticks)[list(TS_METRICS)]
                )
                # 早停复制尾部 ffill；首部（事件/tick 未起）bfill 后再用物理初值兜底
                sub = sub.bfill().ffill()
                for col, v0 in _TS_INIT.items():
                    sub[col] = sub[col].fillna(v0)
                collected.setdefault((key, side), []).append(sub)

    plans = []
    for key, x in plan_vectors.items():
        x_arr = np.asarray(x, dtype=float)
        decisions = {
            "red_repair_teams": int(x_arr[0]),
            "red_spare_stock": int(x_arr[2]),
            "resupply_interval_ticks": int(x_arr[4]),
            "resupply_qty": int(x_arr[5]),
        }
        sides = {}
        for side, sname in _SIDE_NAMES.items():
            stack = np.stack([d.to_numpy(float) for d in collected[(key, side)]])  # (reps,T,4)
            mean = stack.mean(axis=0)
            std = stack.std(axis=0, ddof=1) if reps > 1 else np.zeros_like(mean)
            sides[sname] = {
                m: {"mean": [r4(v) for v in mean[:, j]],
                    "std": [r4(v) for v in std[:, j]]}
                for j, m in enumerate(TS_METRICS)
            }
        plans.append(
            {
                "key": key,
                "label": PLAN_SCENARIO_LABELS.get(key, key),
                "decisions": decisions,
                "sides": sides,
            }
        )

    return write_json(
        {
            "schema": schema("timeseries"),
            "duration_ticks": duration,
            "tick_seconds": TICK_SECONDS,
            "minutes": [r4(t * TICK_SECONDS / 60.0, 2) for t in ticks],
            "reps": reps,
            "plans": plans,
        },
        out_path,
    )


def build_distribution(
    out_path: str | Path,
    scenario,
    x: list[float],
    reps: int = 200,
    seed: int = 20261014,
    kernel: str = "warp",
    device: str = "cuda:0",
    batch_size: int = 256,
) -> Path:
    """knee 方案蒙特卡洛分布：success 频率/直方 + 9 指标五数概括。"""
    from ..surrogate.design import apply_features

    sc = apply_features(scenario, np.asarray(x, dtype=float))
    rows = _run_replicate_rows(sc, reps, seed, kernel, device, batch_size)
    df = pd.DataFrame(rows)

    succ = df["success"].astype(float).to_numpy()
    counts, edges = np.histogram(succ, bins=np.linspace(0.0, 1.0, 11))

    summary = {}
    for t in TARGETS:
        v = df[t].to_numpy(float) if t in df.columns else np.array([])
        v = v[np.isfinite(v)] if len(v) else v
        if len(v) == 0:
            summary[t] = None
            continue
        q = np.quantile(v, [0, 0.25, 0.5, 0.75, 1.0])
        label, kind = METRIC_LABELS[t]
        summary[t] = {
            "label": label,
            "kind": kind,
            "mean": r4(v.mean()),
            "std": r4(v.std(ddof=1)) if len(v) > 1 else 0.0,
            "min": r4(q[0]),
            "q1": r4(q[1]),
            "median": r4(q[2]),
            "q3": r4(q[3]),
            "max": r4(q[4]),
        }

    return write_json(
        {
            "schema": schema("distribution"),
            "n_reps": reps,
            "success_rate": r4(float(succ.mean())),
            "success_count": int(succ.sum()),
            "histogram": {
                "edges": [r4(e) for e in edges],
                "counts": [int(c) for c in counts],
            },
            "summary": summary,
        },
        out_path,
    )


def build_replay(
    rrd_out: str | Path,
    scenario,
    x: list[float],
    seed: int = 20261014,
    kernel: str = "warp",
    device: str = "cuda:0",
    tick_stride: int = 2,
) -> Path:
    """跑一次 knee 方案复制并录制轻量图元 rrd（不嵌 GLB，隔帧；供 web viewer 回放）。"""
    from ..experiment.runner import run_once
    from ..surrogate.design import apply_features
    from ..viz.rerun_view import visualize

    factory, _ = _make_kernel(kernel, device)
    sc = apply_features(scenario, np.asarray(x, dtype=float))
    _, kernel_obj = run_once(sc, seed, factory)
    snap, engagements = kernel_obj.log.to_frames()
    rrd_out = Path(rrd_out)
    rrd_out.parent.mkdir(parents=True, exist_ok=True)
    visualize(
        snap,
        engagements,
        scenario=sc,
        terrain=kernel_obj.terrain,
        rrd_path=rrd_out,
        spawn=False,
        supply=kernel_obj.log.supply_frame(),
        app_id="brigsim-dashboard",
        use_models=False,
        tick_stride=tick_stride,
        embed=True,
    )
    return rrd_out


def _build_sim_outputs(
    *,
    wanted: set[str],
    results: dict,
    out_dir: Path,
    scenario_name: str,
    duration_ticks: int,
    seed: int,
    ts_reps: int,
    dist_reps: int,
    batch_size: int,
    kernel: str,
    device: str,
    make_rrd: bool,
    rrd_out: str | Path,
) -> None:
    scenario = load_scenario(scenario_name)
    if int(scenario.mission.duration_ticks) != int(duration_ticks):
        raise ValueError(
            f"想定 {scenario_name} duration_ticks="
            f"{scenario.mission.duration_ticks} 与参数 {duration_ticks} 不一致"
        )

    if "timeseries" in wanted:
        results["timeseries"] = build_timeseries(
            out_dir / "timeseries.json", scenario,
            {"knee": KNEE_X, "baseline": BASELINE_X},
            reps=ts_reps, seed=seed, kernel=kernel, device=device,
        )
    if "distribution" in wanted:
        results["distribution"] = build_distribution(
            out_dir / "distribution.json", scenario, KNEE_X,
            reps=dist_reps, seed=seed, kernel=kernel, device=device,
            batch_size=batch_size,
        )
    if "replay" in wanted:
        if make_rrd:
            results["replay"] = build_replay(
                rrd_out, scenario, KNEE_X, seed=seed, kernel=kernel, device=device
            )
        else:
            results["replay"] = ("skipped", "--no-rrd 已指定")
