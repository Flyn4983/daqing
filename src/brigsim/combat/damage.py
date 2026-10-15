"""C7-a1 战损分级模块：M（机动受损）/ F（火力受损）/ K（战毁）三级战损判定。

命中判定保留旧口径（roll < pk_eff 击毁/受损段、roll < pk_eff + light_damage_prob
可修复受损段），在此基础上把"受损"细分为部件级：
- 命中段按"兵种角色 × 弹药类别"部件权重抽样损伤部位；命中致命部位
  （车体 hull / 弹药架 ammo_rack）→ K：当场战毁、报废处置（writeoff）；
- 其余部位按 PartSpec.grade 定级 M（行走总成/动力总成）或 F（火控/武器系统）；
  严重度抽样：轻损（light_prob）→ 现地换件（onsite，修复时长减半）；
  重度（severe_prob）→ 后送（rear）；其余 M → 拖救（tow）、F → 自行后送（drive_back）；
- 受损段（旧 0.25 概率段）保持旧口径但同样给出部件（M/F 各半，现地处置）；
- 雷区触雷（resolve_mine）按 mine 权重（专打行走部分）抽样。

未启用 C7-a1 要素（part_stock 空且无 damage_model/part_catalog）时，
resolve_hit 走与旧逻辑完全一致的 roll 判定与日志，零行为漂移（grade 全 0）。

原型标定说明：部件权重/轻中重比例无权威公开数据，S10/S12 标为原型值，
param_catalog.yaml 中留 real_data_slot 供真实毁伤统计替换。
"""

from __future__ import annotations

import numpy as np

from ..data.schema import DamageTableSpec, PartSpec

# 等级与处置编码（state.damage_grade / state.disposition 列存的整数码）
GRADE_NONE, GRADE_M, GRADE_F, GRADE_K = 0, 1, 2, 3
DISP_NONE = 0
DISP_ONSITE = 1       # 现地处置（轻损：换件即可，修复时长减半）
DISP_TOW = 2          # 拖救（M 级：失动，等拖救车，以 tow_delay_ticks 抽象）
DISP_DRIVE_BACK = 3   # 自行后送（F 级：可自驶向修理所）
DISP_REAR = 4         # 后送（重度受损：脱离战线）
DISP_WRITEOFF = 5     # 报废（K 级战毁不可修）

_GRADE_CODE = {"m": GRADE_M, "f": GRADE_F, "k": GRADE_K}

# 分部件备件目录（S11 原型标定：部件 MTBF 参照 S7 冷却系统部件级 MTBF 学术案例
# 与 S1 整机 MTBF 口径等比缩放；repair_ticks 对应"现地换件"量级，轻损减半）
DEFAULT_PART_CATALOG: dict[str, PartSpec] = {
    "mobility": PartSpec(
        part_id="mobility", name="行走总成", grade="m",
        mtbf_ticks=800.0, repair_ticks=4.0,
    ),
    "powerpack": PartSpec(
        part_id="powerpack", name="动力总成", grade="m",
        mtbf_ticks=1200.0, repair_ticks=5.0,
    ),
    "fire_control": PartSpec(
        part_id="fire_control", name="火控系统", grade="f",
        mtbf_ticks=1500.0, repair_ticks=3.0,
    ),
    "weapon": PartSpec(
        part_id="weapon", name="武器系统", grade="f",
        mtbf_ticks=1000.0, repair_ticks=3.0,
    ),
}

# 战损部位分布表（S10/S12 原型标定：致命占比参照 S3/S4 战场抢修返建率
# 反推的量级 + 公开兵棋推演常识；兵种角色按暴露部位推演）
DEFAULT_DAMAGE_TABLE = DamageTableSpec(
    light_damage_prob=0.25,
    fatal_keys=["hull", "ammo_rack"],
    part_weights={
        # 突击/侦察/导弹/工兵：正面装甲对抗为主，命中车体/弹药架比例高
        "assault": {"mobility": 0.16, "powerpack": 0.10, "fire_control": 0.04,
                    "weapon": 0.10, "hull": 0.38, "ammo_rack": 0.22},
        "recon": {"mobility": 0.16, "powerpack": 0.10, "fire_control": 0.04,
                  "weapon": 0.10, "hull": 0.38, "ammo_rack": 0.22},
        "missile": {"mobility": 0.16, "powerpack": 0.10, "fire_control": 0.04,
                    "weapon": 0.10, "hull": 0.38, "ammo_rack": 0.22},
        "engineer": {"mobility": 0.16, "powerpack": 0.10, "fire_control": 0.04,
                     "weapon": 0.10, "hull": 0.38, "ammo_rack": 0.22},
        # 炮兵/保障：背负弹药器材的后舱面积大，动力与武器占比略高
        "artillery": {"mobility": 0.16, "powerpack": 0.14, "fire_control": 0.08,
                      "weapon": 0.12, "hull": 0.32, "ammo_rack": 0.18},
        "support": {"mobility": 0.16, "powerpack": 0.14, "fire_control": 0.08,
                    "weapon": 0.12, "hull": 0.32, "ammo_rack": 0.18},
        # 雷达/防空/电抗/指挥：电子设备密集，火控/电子舱占比高
        "air_defense": {"mobility": 0.10, "powerpack": 0.12, "fire_control": 0.14,
                        "weapon": 0.06, "hull": 0.34, "ammo_rack": 0.24},
        "radar_air": {"mobility": 0.10, "powerpack": 0.12, "fire_control": 0.14,
                      "weapon": 0.06, "hull": 0.34, "ammo_rack": 0.24},
        "radar_cb": {"mobility": 0.10, "powerpack": 0.12, "fire_control": 0.14,
                     "weapon": 0.06, "hull": 0.34, "ammo_rack": 0.24},
        "ew": {"mobility": 0.10, "powerpack": 0.12, "fire_control": 0.14,
               "weapon": 0.06, "hull": 0.34, "ammo_rack": 0.24},
        "c2": {"mobility": 0.10, "powerpack": 0.12, "fire_control": 0.14,
               "weapon": 0.06, "hull": 0.34, "ammo_rack": 0.24},
    },
    ammo_shift={
        # 机关炮（步战）：打车体为主，正面对抗少，行走部暴露多
        "autocannon": {"hull": -0.10, "mobility": 0.06},
        # 反坦克导弹（聚能破甲）：易诱发弹药架、不易精确毁火控
        "atgm": {"ammo_rack": 0.06, "fire_control": -0.06},
        # 间瞄弹（顶攻/破片）：行走部受破片面积大、身管类易损
        "indirect": {"mobility": 0.10, "weapon": -0.08},
        # 空射导弹：俯冲顶攻引爆发射装置
        "air_missile": {"ammo_rack": 0.08, "hull": -0.06},
        # 地雷：整组权重覆盖（专打行走部分，致命 0.15）
        "mine": {"mobility": 0.55, "powerpack": 0.30, "hull": 0.10,
                 "ammo_rack": 0.05, "fire_control": 0.0, "weapon": 0.0},
    },
    light_prob=0.30,
    severe_prob=0.15,
)

# 同等级内按自然故障频率（1/mtbf）抽样（自然故障只落这四个可修部件）
_REPAIRABLE_PARTS = ("mobility", "powerpack", "fire_control", "weapon")


def sample_part(
    role: str, ammo_class: str, rng: np.random.Generator, table: DamageTableSpec | None = None
) -> str:
    """按兵种角色 × 弹药类别加权抽样一个损伤部件名（可单测）。"""
    table = table or DEFAULT_DAMAGE_TABLE
    base = table.part_weights.get(role) or DEFAULT_DAMAGE_TABLE.part_weights["assault"]
    if ammo_class == "mine":
        # mine 键整组权重覆盖（专打行走部分）
        group = table.ammo_shift.get("mine") or DEFAULT_DAMAGE_TABLE.ammo_shift["mine"]
        w = {k: v for k, v in group.items() if v > 0.0}
    else:
        w = dict(base)
        shift = table.ammo_shift.get(ammo_class)
        if shift is not None:
            for k, v in shift.items():
                w[k] = max(0.02, w.get(k, 0.0) + v)
    keys = list(w)
    probs = np.asarray([w[k] for k in keys], dtype=np.float64)
    probs /= probs.sum()
    return keys[int(rng.choice(len(keys), p=probs))]


def _sample_repairable(
    rng: np.random.Generator, catalog: dict[str, PartSpec], grade: str | None = None
) -> str:
    """按 1/mtbf 权重抽样一个可修部件；grade 给定时仅在该等级内抽样。"""
    cand = [p for p in catalog if p in _REPAIRABLE_PARTS
            and (grade is None or catalog[p].grade == grade)]
    if not cand:
        cand = list(catalog)
    probs = np.asarray([1.0 / float(catalog[p].mtbf_ticks) for p in cand], dtype=np.float64)
    probs /= probs.sum()
    return cand[int(rng.choice(len(cand), p=probs))]


def grade_of(part: str, table: DamageTableSpec | None = None,
             catalog: dict[str, PartSpec] | None = None) -> str:
    """部件名 → m/f/k 等级；致命部件恒为 k，其余按 PartSpec.grade。"""
    table = table or DEFAULT_DAMAGE_TABLE
    if part in table.fatal_keys:
        return "k"
    catalog = catalog or DEFAULT_PART_CATALOG
    spec = catalog.get(part)
    return spec.grade if spec is not None else "m"


class DamageModel:
    """战损判定器：旧 roll 逻辑（未启用 a1）与 C7-a1 分部件分级共存的统一入口。

    引用内核的 scenario/state/sustain/log/rng，不使用全局随机源；
    Warp 内核因 C7-a1 要素守卫报错，不构造本模型。
    """

    def __init__(self, kernel) -> None:
        self.k = kernel
        self.state = kernel.state
        self.rng = kernel.rng
        spec = kernel.scenario.sustainment
        # a1 模式开关：part_stock 非空 或 damage_model/part_catalog 显式给出
        self.enabled = (
            bool(spec.part_stock) or spec.damage_model is not None
            or spec.part_catalog is not None
        )
        self.table = spec.damage_model if spec.damage_model is not None \
            else DEFAULT_DAMAGE_TABLE
        self.catalog = spec.part_catalog if spec.part_catalog is not None \
            else DEFAULT_PART_CATALOG

    # ---- 着陆点判定 ----

    def resolve_hit(
        self,
        shooter_name: str,
        target: int,
        pk_eff: float,
        ammo_class: str,
        weapon: str = "direct",
        always_log: bool = True,
    ) -> tuple[bool, bool]:
        """一次射击/弹着判定：返回 (killed, hit)。

        - 旧模式（未启用 a1）：与既有 roll 逻辑逐字节等价（日志 grade=""）；
        - a1 模式：命中段按部件权重抽样分级，受损段按 M/F 各半现地处置。
        always_log=False 时未命中也记 engagement（匹配各调用点的旧行为）。
        """
        s = self.state
        t = int(target)
        roll = self.rng.random()
        grade_log = ""
        if roll < pk_eff:
            if not self.enabled:
                s.alive[t] = False
                s.damaged[t] = False
                killed, damaged = True, False
            else:
                part = sample_part(str(s.role[t]), ammo_class, self.rng, self.table)
                grade = grade_of(part, self.table, self.catalog)
                if grade == "k":
                    self._apply_kill(t, part)
                    killed, damaged = True, False
                    grade_log = "k"
                else:
                    severity = self.rng.random()
                    if severity < self.table.light_prob:
                        disp = DISP_ONSITE
                    elif severity < self.table.light_prob + self.table.severe_prob:
                        disp = DISP_REAR
                    else:
                        disp = DISP_TOW if grade == "m" else DISP_DRIVE_BACK
                    self._apply_damage(t, grade, part, disp)
                    killed, damaged = False, True
                    grade_log = grade
        elif roll < pk_eff + self.table.light_damage_prob:
            if not self.enabled:
                self.k.sustain.combat_damage(t)
            else:
                # 旧 0.25 受损段口径：轻损，M/F 各半，现地换件
                grade = "m" if self.rng.random() < 0.5 else "f"
                part = _sample_repairable(self.rng, self.catalog, grade)
                self._apply_damage(t, grade, part, DISP_ONSITE)
                grade_log = grade
            killed, damaged = False, True
        else:
            killed, damaged = False, False
        if killed or damaged or always_log:
            self.k.log.engagement(
                int(s.tick), shooter_name, str(s.unit_ids[t]), killed, damaged,
                weapon=weapon, grade=grade_log,
            )
        return killed, killed or damaged

    def resolve_mine(self, i: int) -> None:
        """雷场触雷判定（口径与旧的 mine_damage 一致：K 0.03 / 受损 0.12）。

        a1 模式下受损部位按 mine 权重抽样（专打行走部分）；其余情况不记日志。
        """
        s = self.state
        roll = self.rng.random()
        if roll < 0.03:
            s.alive[i] = False
            s.damaged[i] = False
            if self.enabled:
                part = sample_part(str(s.role[i]), "mine", self.rng, self.table)
                self._apply_kill(i, part)
                grade_log = "k"
            else:
                grade_log = ""
            self.k.log.engagement(s.tick, "minefield", str(s.unit_ids[i]), True, False,
                                  weapon="mine", grade=grade_log)
        elif roll < 0.12:
            if not self.enabled:
                self.k.sustain.combat_damage(int(i))
                grade_log = ""
            else:
                part = sample_part(str(s.role[i]), "mine", self.rng, self.table)
                grade = grade_of(part, self.table, self.catalog)
                if grade == "k":
                    self._apply_kill(i, part)
                    self.k.log.engagement(s.tick, "minefield", str(s.unit_ids[i]),
                                          True, False, weapon="mine", grade="k")
                    return
                # 地雷毁伤绝不自行后送：轻损现地处置，否则拖救
                disp = DISP_ONSITE if self.rng.random() < self.table.light_prob \
                    else DISP_TOW
                self._apply_damage(int(i), grade, part, disp)
                grade_log = grade
            self.k.log.engagement(s.tick, "minefield", str(s.unit_ids[i]), False, True,
                                  weapon="mine", grade=grade_log)

    # ---- 内部落地 ----

    def _apply_kill(self, i: int, part: str) -> None:
        s = self.state
        s.alive[i] = False
        s.damaged[i] = False
        s.damage_grade[i] = GRADE_K
        s.part_id[i] = part
        s.disposition[i] = DISP_WRITEOFF

    def _apply_damage(self, i: int, grade: str, part: str, disp: int) -> None:
        self.k.sustain.combat_damage(
            int(i), grade=_GRADE_CODE[grade], part=part, disposition=disp
        )