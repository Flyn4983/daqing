"""代理模型特征空间：保障业务"旋钮" + 不可控战场情景因子。

设计对齐比赛评审三原则（2026-10-14）：
- 落地：前 6 个特征是合成旅保障参谋**实际可调**的资源旋钮（抢修组数、备件
  基数、前送周期与批量），输出 GJB 口径四率 + 任务完成率；
- 可推广：特征在标定基线上做**乘子/增量扰动**，不绑死 example_brigade 的
  具体数值，换想定/装备参数后重采样重训即可复用；
- 域取自 templates/param_catalog.yaml（C2 参数目录），取其合理子区间以保证
  训练点都在"现实可行"域内，窄于 catalog 全域时已注释说明。

另：pk 乘子是"敌情/交战不确定"情景因子（参谋不可控但需做态势研判），
detect/speed 等其他交战因子经 M3 灵敏度验证与 pk 高度共线，首版不纳入。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..data.schema import Scenario, SideModifiers

# ---- 特征定义：(名称, 下界, 上界, 是否整数) --------------------------------
# 名称约定：保障旋钮名与 SustainmentSpec 字段对应；*_scale 为基线乘子。
FEATURES: list[tuple[str, float, float, bool]] = [
    # —— 保障资源旋钮（参谋可调，C4 NSGA-II 的决策变量区）——
    ("red_repair_teams", 2.0, 10.0, True),       # 红方并行抢修组数
    ("blue_repair_teams", 1.0, 8.0, True),       # 蓝方抢修组数（对手，情景）
    ("red_spare_stock", 4.0, 30.0, True),        # 红方初始备件基数（件）
    ("blue_spare_stock", 2.0, 20.0, True),       # 蓝方初始备件基数
    ("resupply_interval_ticks", 10.0, 40.0, True),  # 周期前送间隔（tick=30s）
    ("resupply_qty", 2.0, 16.0, True),           # 每次前送备件数（件）
    # —— 装备/战场情景因子（不可控，做态势研判与鲁棒性分析用）——
    ("mtbf_scale", 0.5, 2.0, False),             # 全平台 MTBF 乘子（装备可靠性水平）
    ("red_pk_scale", 0.7, 1.3, False),           # 红方交战效能不确定带
    ("blue_pk_scale", 0.7, 1.3, False),          # 蓝方（敌情）交战效能不确定带
    ("visibility", 0.5, 1.0, False),             # 气象能见度乘子
]
FEATURE_NAMES = [f[0] for f in FEATURES]

# ---- 预测目标：四率（分方）+ 任务完成率 -----------------------------------
# 故障率取"每 100 装备小时"口径（failure_rate_100h_*），比 /单位·tick 更贴
# 装备管理业务语言；概率类目标天然落在 [0,1]，训练后做物理界 clip。
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


def apply_features(scenario: Scenario, x: np.ndarray) -> Scenario:
    """把一行特征（物理单位值）施加到想定，返回新 Scenario（不改原对象）。

    pk/mtbf 乘子在想定既有标定烘焙值上**累乘**（与 sensitivity.apply_point
    同一约定）；保障旋钮直接覆盖 sustainment 对应字段。
    地形/编制/任务不变（代理模型只学"同想定结构、不同保障与情景"的响应面）。
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
    env = scenario.environment.model_copy(
        update={"visibility_factor": float(vals["visibility"])}
    )
    sustain = scenario.sustainment.model_copy(
        update={
            "repair_teams": {
                "red": int(vals["red_repair_teams"]),
                "blue": int(vals["blue_repair_teams"]),
            },
            "spare_stock": {
                "red": int(vals["red_spare_stock"]),
                "blue": int(vals["blue_spare_stock"]),
            },
            "resupply_interval_ticks": int(vals["resupply_interval_ticks"]),
            "resupply_qty": int(vals["resupply_qty"]),
        }
    )
    return scenario.model_copy(
        update={
            "platform_types": ptypes,
            "environment": env,
            "sustainment": sustain,
            "side_modifiers": {
                "red": _mod("red", vals["red_pk_scale"]),
                "blue": _mod("blue", vals["blue_pk_scale"]),
            },
        }
    )
