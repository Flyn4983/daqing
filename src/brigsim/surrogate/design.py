"""代理模型特征空间（C7-a3 口径）：保障业务"旋钮" + 不可控战场情景因子。

C7-a3 随 a1/a2 的战损分级、分部件备件与保障实体化重建：
- 参谋可调的不再是聚合"备件池"，而是 **7 个 a2 口径决策变量**：
  抢修组数、装甲抢救车数、器材补给车数、携行档基数档级、运行档基数档级、
  前送波次节拍（tick）、每波每部件件数；
- 器材按 4 类部件（行走/动力/火控/武器）分档，档级到件数向量的映射见
  CARRY_LEVELS/OPERATING_LEVELS（用档级而非 8 个独立部件旋钮，把决策维数
  控制在小规模 Mesa 数据集可学的范围内，同时保留业务上"基数档位"语义）；
- 抢救车/器材车数量直接改编制（forces 中 rarv/rsup 编组的实体数），
  实体本身计入参战总数，增减会同时改变四率分母，符合实战；
- 3 个不可控情景因子：MTBF 乘子、红蓝交战效能乘子（蓝方保障沿用 a1 抽象，
  不再作为旋钮——我方参谋无权调配敌方资源）。

域取自 templates/param_catalog.yaml 的合理子区间；在标定基线上做扰动，
不绑死 example_brigade 的具体数值，换想定/装备参数后重采样重训即可复用。

旧想定兼容：apply_features 对没有 part_stock/support_echelon/对应保障
编组的想定自动跳过相关改写（只改抢修组/乘子），保证零漂移。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..data.schema import Scenario, SideModifiers, UnitSpec

# ---- 部件口径（与 combat/damage.py 部件目录一致）--------------------------
PART_ORDER = ("mobility", "powerpack", "fire_control", "weapon")

# 携行档（随队现地换件）5 档：{部件: 件数}，档级 2 = v4 标定基线
CARRY_LEVELS: list[dict[str, int]] = [
    {"mobility": 3, "powerpack": 2, "fire_control": 3, "weapon": 4},    # 精简
    {"mobility": 4, "powerpack": 4, "fire_control": 4, "weapon": 6},    # 偏低
    {"mobility": 6, "powerpack": 5, "fire_control": 6, "weapon": 8},    # 基线
    {"mobility": 8, "powerpack": 7, "fire_control": 8, "weapon": 10},   # 加强
    {"mobility": 10, "powerpack": 8, "fire_control": 10, "weapon": 13}, # 充裕
]
# 运行档（营中继抢修点常备）4 档：档级 2 = v4 标定基线
OPERATING_LEVELS: list[dict[str, int]] = [
    {"mobility": 2, "powerpack": 1, "fire_control": 2, "weapon": 2},    # 精简
    {"mobility": 3, "powerpack": 2, "fire_control": 3, "weapon": 4},    # 偏低
    {"mobility": 4, "powerpack": 3, "fire_control": 4, "weapon": 5},    # 基线
    {"mobility": 6, "powerpack": 5, "fire_control": 6, "weapon": 7},    # 加强
]

# 保障实体编组：特征名 → (unit_id 前缀, 平台类型)
ENTITY_GROUPS = {
    "red_arv": ("rarv", "arv"),
    "red_supply_veh": ("rsup", "supply_veh"),
}

# ---- 特征定义：(名称, 下界, 上界, 是否整数) --------------------------------
FEATURES: list[tuple[str, float, float, bool]] = [
    # —— 保障资源旋钮（参谋可调，C4 NSGA-II 的决策变量区，共 7 维）——
    ("red_repair_teams", 2.0, 10.0, True),       # 并行抢修组数
    ("red_arv", 2.0, 6.0, True),                 # 装甲抢救车数量（伴随拖救）
    ("red_supply_veh", 1.0, 3.0, True),          # 器材补给车数量（库站前送）
    ("carry_level", 0.0, 4.0, True),             # 携行档基数档级（0-4）
    ("operating_level", 0.0, 3.0, True),         # 运行档基数档级（0-3）
    ("wave_interval_ticks", 10.0, 40.0, True),   # 前送波次节拍（tick=30s）
    ("wave_qty", 1.0, 4.0, True),                # 每波每部件器材件数
    # —— 装备/战场情景因子（不可控，做态势研判与鲁棒性分析用，共 3 维）——
    ("mtbf_scale", 0.5, 2.0, False),             # 全平台 MTBF 乘子（装备可靠性）
    ("red_pk_scale", 0.7, 1.3, False),           # 红方交战效能不确定带
    ("blue_pk_scale", 0.7, 1.3, False),          # 蓝方（敌情）交战效能不确定带
]
FEATURE_NAMES = [f[0] for f in FEATURES]
N_DECISION = 7

# ---- 预测目标：四率（分方）+ 任务完成率 -----------------------------------
TARGETS = [
    "success",
    "attrition_red",
    "attrition_blue",
    "readiness_red",
    "readiness_blue",
    "repair_rate_red",
    "repair_rate_blue",
    "failure_rate_100h_red",
    "failure_rate_100h_blue",
]
# 输出物理界（预测 clip 与数据校验用）：(低, 高)；None 端=不截
PROB_TARGETS = [t for t in TARGETS if not t.startswith("failure_rate_100h")]
TARGET_BOUNDS: dict[str, tuple[float, float | None]] = {t: (0.0, 1.0) for t in PROB_TARGETS}
TARGET_BOUNDS["failure_rate_100h_red"] = (0.0, None)
TARGET_BOUNDS["failure_rate_100h_blue"] = (0.0, None)


@dataclass(frozen=True)
class FeatureSpace:
    """特征空间句柄：名称/域/类型，供数据集、模型、C4 优化共用。"""

    features: tuple[tuple[str, float, float, bool], ...] = tuple(FEATURES)

    @property
    def names(self) -> list[str]:
        return [f[0] for f in self.features]

    @property
    def n_dim(self) -> int:
        return len(self.features)

    def scale(self, u: np.ndarray) -> np.ndarray:
        """把 [0,1]^d 的点线性映射到特征域，整数维就近取整。"""
        x = np.empty_like(u, dtype=float)
        for j, (_, lo, hi, is_int) in enumerate(self.features):
            v = lo + u[:, j] * (hi - lo)
            x[:, j] = np.rint(v) if is_int else v
        return x

    def clip(self, x: np.ndarray) -> np.ndarray:
        """把外部给定点（如 C4 优化解）截回可行域并规整整数维。"""
        x = np.asarray(x, dtype=float).copy()
        for j, (_, lo, hi, is_int) in enumerate(self.features):
            np.clip(x[:, j], lo, hi, out=x[:, j])
            if is_int:
                x[:, j] = np.rint(x[:, j])
        return x


SPACE = FeatureSpace()


def sample_lhs(n: int, seed: int = 20261014) -> np.ndarray:
    """拉丁超立方抽样（空间填充性好，代理模型训练集首选）。"""
    from scipy.stats import qmc

    sampler = qmc.LatinHypercube(d=SPACE.n_dim, seed=seed)
    return SPACE.scale(sampler.random(n=n))


def sample_random(n: int, seed: int = 20261014) -> np.ndarray:
    """独立均匀随机抽样（holdout 测试集用，与 LHS 训练集不同分布机制）。"""
    rng = np.random.default_rng(seed)
    return SPACE.scale(rng.random((n, SPACE.n_dim)))


def _resize_entity_group(
    forces: list[UnitSpec], prefix: str, ptype: str, target: int
) -> list[UnitSpec]:
    """把指定 id_prefix 的保障编组扩/缩到 target 个，返回新的红方单位列表。

    缩减：按 unit_id 排序裁掉序号最大者；扩编：克隆本组最后一个实体，
    沿编队侧向（本组平均侧向间距）依次排列，id 顺延。编组不存在时不动。
    """
    idx = [i for i, u in enumerate(forces) if u.unit_id.startswith(prefix)]
    if not idx:
        return forces
    idx.sort(key=lambda i: forces[i].unit_id)
    group = [forces[i] for i in idx]
    keep = group[:target]
    # 扩编：以组内 y 向平均间距为步长（line 编队按 lateral 展开）
    ys = sorted(float(u.pos[1]) for u in group)
    gaps = [b - a for a, b in zip(ys, ys[1:]) if b - a > 1]
    step = abs(sum(gaps) / len(gaps)) if gaps else 900.0
    template = group[-1]
    extra: list[UnitSpec] = []
    for k in range(len(group), target):
        uid = f"{prefix}{k + 1:02d}"
        pos = (float(template.pos[0]), float(ys[-1]) + step * (k - len(group) + 2))
        extra.append(template.model_copy(update={"unit_id": uid, "pos": pos}))
    # 无论扩缩：把整组替换为 keep+extra，插回首组位置（不依赖组在列表中连续）
    group_ids = {u.unit_id for u in group}
    new_group = keep + extra
    out: list[UnitSpec] = []
    inserted = False
    for u in forces:
        if u.unit_id in group_ids:
            if not inserted:
                out.extend(new_group)
                inserted = True
        else:
            out.append(u)
    return out


def apply_features(scenario: Scenario, x: np.ndarray) -> Scenario:
    """把一行特征（物理单位值）施加到想定，返回新 Scenario（不改原对象）。

    pk/mtbf 乘子在想定既有标定烘焙值上**累乘**（与 sensitivity.apply_point
    同一约定）；保障旋钮按 a2 口径覆盖（旧想定缺相应结构时跳过该旋钮）。
    地形/编制主体/任务不变（代理只学"同想定结构、不同保障与情景"的响应面）。
    """
    x = np.asarray(x, dtype=float).reshape(-1)
    vals = dict(zip(SPACE.names, x, strict=True))

    def _mod(side: str, pk_s: float) -> SideModifiers:
        m0 = scenario.side_modifiers.get(side, SideModifiers())
        return m0.model_copy(update={"pk_scale": m0.pk_scale * float(pk_s)})

    ptypes = {
        name: p.model_copy(
            update={"mtbf_ticks": p.mtbf_ticks * float(vals["mtbf_scale"])}
        )
        for name, p in scenario.platform_types.items()
    }

    # ---- 保障旋钮（按想定实际结构条件生效）----
    sus0 = scenario.sustainment
    sus_update: dict = {
        "repair_teams": {
            "red": int(vals["red_repair_teams"]),
            "blue": int(sus0.repair_teams.get("blue", 1)),
        },
    }
    # 分部件三档：仅对已启用战损分级的想定覆盖红方档级
    if sus0.part_stock.get("red") or sus0.part_catalog or sus0.damage_model:
        part_stock = {side: dict(v) for side, v in sus0.part_stock.items()}
        part_stock["red"] = dict(CARRY_LEVELS[int(vals["carry_level"])]).copy()
        sus_update["part_stock"] = part_stock
    # 保障实体化波次：仅对声明 support_echelon 的方别生效（原型只有红方）
    ech0 = sus0.support_echelon
    if ech0 is not None:
        op_stock = {side: dict(v) for side, v in ech0.operating_stock.items()}
        op_stock["red"] = dict(OPERATING_LEVELS[int(vals["operating_level"])]).copy()
        sus_update["support_echelon"] = ech0.model_copy(
            update={
                "wave_interval_ticks": int(vals["wave_interval_ticks"]),
                "wave_qty": int(vals["wave_qty"]),
                "operating_stock": op_stock,
            }
        )
    sustain = sus0.model_copy(update=sus_update)

    # ---- 保障实体编组数量（改的是展开后的 forces）----
    forces_red = list(scenario.forces.get("red", []))
    changed_forces = False
    for fname, (prefix, ptype) in ENTITY_GROUPS.items():
        target = int(vals[fname])
        new_forces = _resize_entity_group(forces_red, prefix, ptype, target)
        if new_forces is not forces_red:
            forces_red = new_forces
            changed_forces = True

    update = {
        "platform_types": ptypes,
        "sustainment": sustain,
        "side_modifiers": {
            "red": _mod("red", vals["red_pk_scale"]),
            "blue": _mod("blue", vals["blue_pk_scale"]),
        },
    }
    if changed_forces:
        update["forces"] = {"red": forces_red, "blue": list(scenario.forces.get("blue", []))}
    return scenario.model_copy(update=update)
