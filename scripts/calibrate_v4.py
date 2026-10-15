"""C7-a2 v4 粗标定脚本（CRN 分级搜索，非全量 DE）。

背景：v3 原型参数（致命占比 0.60、蓝方 pk_scale 1.0、轻损率 0.30）在 a0/a1
机制下任务成功率 0/N（红方均可破障但冲不到纵深目标）。v4 目标：粗标定到
成功率 0.5-0.75 区间，为 C7-a3 代理模型重训提供均衡基线。

方法（计算预算约束下的选择）：
- CRN（公共随机数）：各候选配置使用同一组种子，降低配置间对比方差；
- 分级粗搜：按"致命缩放→蓝方火力→轻损率"先跑 3 档候选，再围绕命中区间
  的配置加密；单局 Mesa 推演约 11s，全量 DE（20×15×8 复制）在本会话
  预算内不可行，故明确标注为粗标定，待真实数据与 GPU 批推演后精标。
- 输出：各配置成功率/红方存活/蓝方存活/阶段分布，据此手工选定 v4 落盘参数。

用法：
    .venv/Scripts/python scripts/calibrate_v4.py
"""

from __future__ import annotations

import copy
import time

import numpy as np

from brigsim.combat.damage import DEFAULT_DAMAGE_TABLE
from brigsim.combat.mesa_kernel import MesaKernel
from brigsim.data.provider import YamlFileProvider

SEEDS = list(range(100, 108))  # 8 个 CRN 种子

# 第二轮候选（结构修复：掘壕固守 + 迫击炮 6km + 蓝直瞄 41→31 + 火箭弹 2→4 之后）：
# (名称, 致命权重乘子, 蓝方 pk_scale, 轻损率, 重度率)
CONFIGS = [
    ("v4_D_struct", 1.00, 1.00, 0.30, 0.15),   # 默认毁伤表，纯结构平衡
    ("v4_E_fatal50", 0.50, 1.00, 0.35, 0.15),  # 致命占比减半，多走 M/F 抢修链
    ("v4_F_combo", 0.50, 0.85, 0.40, 0.14),    # 致命减半 + 蓝火力轻度折减
]


def scaled_damage_table(fatal_scale: float, light_prob: float,
                        severe_prob: float):
    """致命部件（hull/ammo_rack）权重乘 fatal_scale 后整体归一化；
    ammo_shift 在 sample_part 内加性叠加后再归一化，无需改动。"""
    table = DEFAULT_DAMAGE_TABLE.model_copy(deep=True)
    new_weights = {}
    for role, w in table.part_weights.items():
        w = dict(w)
        for key in table.fatal_keys:
            w[key] *= fatal_scale
        total = sum(w.values())
        new_weights[role] = {key: float(val / total) for key, val in w.items()}
    table.part_weights = new_weights
    table.light_prob = light_prob
    table.severe_prob = severe_prob
    return table


def apply_config(sc, fatal_scale: float, blue_pk: float,
                 light_prob: float, severe_prob: float):
    sc.sustainment.damage_model = scaled_damage_table(
        fatal_scale, light_prob, severe_prob
    )
    sc.side_modifiers["blue"].pk_scale = blue_pk
    return sc


def evaluate(name: str, params, seeds=SEEDS) -> dict:
    base = YamlFileProvider().load_scenario("example_brigade")
    succ, red_alive, blue_alive, phases = [], [], [], []
    t0 = time.time()
    for seed in seeds:
        sc = apply_config(copy.deepcopy(base), *params)
        k = MesaKernel(sc, seed=seed)
        while not k.finished:
            k.step()
        s = k.state
        succ.append(bool(k.mission_success()))
        red_alive.append(int((s.alive & (s.side == 0)).sum()))
        blue_alive.append(int((s.alive & (s.side == 1)).sum()))
        phases.append(int(s.phase))
    dt = time.time() - t0
    result = {
        "config": name,
        "n": len(seeds),
        "success_rate": float(np.mean(succ)),
        "red_alive_mean": float(np.mean(red_alive)),
        "blue_alive_mean": float(np.mean(blue_alive)),
        "phase_hist": {p: phases.count(p) for p in sorted(set(phases))},
        "seconds": round(dt, 1),
    }
    print(
        f"{name:14s} success={result['success_rate']:.2f} "
        f"red={result['red_alive_mean']:.1f} blue={result['blue_alive_mean']:.1f} "
        f"phases={result['phase_hist']} ({dt:.0f}s)",
        flush=True,
    )
    return result


if __name__ == "__main__":
    print(f"v4 粗标定：{len(CONFIGS)} 配置 × {len(SEEDS)} CRN 种子", flush=True)
    rows = [evaluate(name, p) for name, *p in CONFIGS]
    print("\n汇总（目标 success ∈ [0.50, 0.75]）：")
    for r in rows:
        print(r)
