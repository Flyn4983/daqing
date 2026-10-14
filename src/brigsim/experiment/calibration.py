"""M3 参数标定：把想定级旋钮调到公开参考目标带内。

为什么需要：竞争修复后旅级基线 512 复制 success=1.000（天花板），
结束时刻诊断显示红方 p50=45 tick 即完成三阶段（duration=220 tick，
时间余量约 4 倍）——时间压力未绑定，灵敏度对 success 失效。

参考依据（DEVLOG 留链接，原型阶段均为显式假设，真实数据到位后重标定）：
- 越野速度：MBT 最大越野速度约 48–55 km/h（M1A1 48.3、豹 2 约 55），
  火力下接敌/突破运动速率显著低于最大值；z_speed 是"接敌运动系数"，
  乘到标称速度上，而非重新定义装备最大越野速度；
- 单发毁伤：ATGM 单发命中概率公开值 0.8–0.98（动对动更低），
  模型 pk 是交战周期等效毁伤概率，本轮不动 pk；
- 攻防交换：攻坚"3:1 兵力规则"与 Dupuy 攻坚高方差记录——红:蓝
  104:63≈1.65:1 进攻既设防御，按经验应是苦战而非必胜，故 success
  目标带取 0.50–0.75（宽幅，承认真实数据的高方差）。

方法：公共随机数（CRN）蒙特卡洛 + scipy.optimize.differential_evolution
（scipy 随 SALib 已在环境中，不引新依赖）；目标函数是各指标均值
偏离目标带的归一化平方罚，带内罚 0。
"""

from __future__ import annotations

import time

import numpy as np

from ..data.schema import Scenario

# 原型阶段标定目标带（指标名, 下限, 上限, 归一化带宽）
BANDS: dict[str, tuple[float, float, float]] = {
    "success": (0.50, 0.75, 0.25),
    "attrition_red": (0.45, 0.70, 0.25),
    # 蓝战损上界 0.85：既设阵地被夺时防御方分数损失普遍很高（战史常见 0.8+）
    "attrition_blue": (0.50, 0.85, 0.35),
}

# 旋钮：名称 -> (下界, 上界)；顺序即 x 向量顺序
KNOB_BOUNDS: dict[str, tuple[float, float]] = {
    "z_speed": (0.60, 0.95),     # 接敌运动系数（乘标称最大越野速度）
    "duration": (50.0, 110.0),   # 任务持续时间（tick，30s/tick）
    "z_blue_pk": (1.0, 1.8),     # 蓝方既设防御火力乘子（方别不对称）
}
KNOB_NAMES = tuple(KNOB_BOUNDS)


def apply_knobs(scenario: Scenario, x: np.ndarray | tuple[float, ...]) -> Scenario:
    """把标定旋钮施加到想定（不改原对象）。"""
    from ..data.schema import SideModifiers

    knobs = dict(zip(KNOB_NAMES, x))
    z = float(knobs["z_speed"])
    ptypes = {
        name: p.model_copy(update={"speed_m_per_tick": p.speed_m_per_tick * z})
        for name, p in scenario.platform_types.items()
    }
    mission = scenario.mission.model_copy(
        update={"duration_ticks": int(round(float(knobs["duration"])))}
    )
    side_modifiers = dict(scenario.side_modifiers)
    side_modifiers["blue"] = SideModifiers(pk_scale=float(knobs["z_blue_pk"]))
    return scenario.model_copy(
        update={
            "platform_types": ptypes,
            "mission": mission,
            "side_modifiers": side_modifiers,
        }
    )


def band_penalty(means: dict[str, float]) -> float:
    """指标均值落在目标带内罚 0，带外按归一化偏离平方罚。"""
    total = 0.0
    for key, (lo, hi, width) in BANDS.items():
        v = float(means[key])
        d = max(lo - v, 0.0, v - hi) / width
        total += d * d
    return total


def point_means(
    scenario: Scenario,
    x: np.ndarray | tuple[float, ...],
    seeds: list[int],
    device: str = "cuda:0",
) -> dict[str, float]:
    """单组旋钮的 CRN 蒙特卡洛指标均值（同一批 seeds 跨评估点复用）。"""
    from ..combat.warp_batch import run_batch

    sc = apply_knobs(scenario, x)
    rows = run_batch(sc, seeds, device=device)
    keys = ("success", "attrition_red", "attrition_blue", "readiness_red", "readiness_blue")
    return {k: float(np.mean([r[k] for r in rows])) for k in keys}


def calibrate(
    scenario: Scenario,
    reps: int = 128,
    base_seed: int = 20261014,
    device: str = "cuda:0",
    maxiter: int = 10,
    popsize: int = 8,
    verbose: bool = True,
) -> dict:
    """差分进化搜索目标带内旋钮；返回最佳点、指标均值与罚值。

    结果须用未参与搜索的种子（更大 reps）复验后再写回想定。
    """
    from scipy.optimize import differential_evolution

    seeds = [base_seed + r for r in range(reps)]
    bounds = [KNOB_BOUNDS[name] for name in KNOB_NAMES]
    cache: dict[tuple, tuple[float, dict[str, float]]] = {}
    history: list[tuple[float, float, int]] = []  # (wall, penalty, duration)

    def objective(zx: np.ndarray) -> float:
        z = float(zx[0])
        dur = int(round(float(zx[1])))
        zb = round(float(zx[2]), 4)
        key = (round(z, 4), dur, zb)
        if key in cache:
            return cache[key][0]
        means = point_means(scenario, (z, float(dur), float(zb)), seeds, device=device)
        pen = band_penalty(means)
        cache[key] = (pen, means)
        history.append((time.perf_counter(), pen, dur))
        if verbose:
            print(
                f"  eval {len(cache):3d}: z_speed={z:.3f} dur={dur:3d} z_blue_pk={zb:.2f} "
                f"pen={pen:.4f} succ={means['success']:.3f} "
                f"aR={means['attrition_red']:.3f} aB={means['attrition_blue']:.3f}",
                flush=True,
            )
        return pen

    t0 = time.perf_counter()
    res = differential_evolution(
        objective,
        bounds,
        seed=base_seed,
        maxiter=maxiter,
        popsize=popsize,
        tol=1e-3,
        polish=False,
        updating="immediate",
    )
    best_x = (
        float(res.x[0]),
        int(round(float(res.x[1]))),
        round(float(res.x[2]), 3),
    )
    # res.x 可能落在评估网格的取整邻域（如 duration 边界），直接重算最稳
    best_means = point_means(scenario, best_x, seeds, device=device)
    best_pen = band_penalty(best_means)
    out = {
        "x": dict(zip(KNOB_NAMES, best_x)),
        "penalty": best_pen,
        "means": best_means,
        "n_eval": len(cache),
        "wall_s": time.perf_counter() - t0,
    }
    if verbose:
        print(f"标定结束：{out['n_eval']} 评估点，{out['wall_s']:.0f}s，"
              f"最佳 {out['x']} pen={best_pen:.4f}")
    return out
