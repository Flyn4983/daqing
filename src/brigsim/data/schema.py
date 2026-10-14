"""想定与参数的 Pydantic schema（M0 粗版，后续随数据资产扩展）。

约定：参数与代码分离；平台类型参数带来源元数据，支撑后续校正与版本化。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

SideName = Literal["red", "blue"]


class ParamMeta(BaseModel):
    """参数来源溯源信息（M3 标定闭环用）。"""

    source: str = "example"
    version: str = "0.1"
    confidence: float = Field(0.5, ge=0.0, le=1.0)
    updated_at: str | None = None


class Terrain(BaseModel):
    # 高程来源：synthetic=FBM 程序噪声；geotiff=读取 DEM（数组线性拉伸到 size_m）
    source: Literal["synthetic", "geotiff"] = "synthetic"
    # geotiff 相对路径相对于想定 YAML 所在目录（由 Provider 解析）
    geotiff_path: str | None = None
    # 通行性栅格（0=不可通行 … 1=道路），与 DEM 同网格约定；None=全域 1.0
    passability_path: str | None = None
    # 方形战场边界（米）
    size_m: tuple[float, float] = (10_000.0, 10_000.0)
    # 内核机动/插值使用的网格分辨率（GeoTIFF 会重采样到该尺度）
    grid_n: int = 65
    # synthetic 专用 FBM 参数
    noise_seed: int = 42
    noise_scale: float = 3.0
    octaves: int = 4
    base_height: float = 60.0
    amplitude: float = 140.0
    water_level: float = 10.0


class Environment(BaseModel):
    weather: Literal["clear", "rain", "fog", "snow"] = "clear"
    # 对探测距离的修正系数（雨/雾/雪 <1），同时驱动可视化粒子
    visibility_factor: float = 1.0


class MissionPhase(BaseModel):
    """一个任务阶段：红方进占该目标点（任一存活单位进入半径）即切换下一阶段。"""

    name: str
    objective: tuple[float, float]
    success_radius_m: float = 300.0


class Mission(BaseModel):
    duration_ticks: int = 120
    dt_seconds: int = 30
    objective: tuple[float, float] = (8_000.0, 5_000.0)
    success_radius_m: float = 300.0
    # 多阶段任务（进攻出发阵地 → 突破前沿 → 纵深要点…）；None 时退化为单目标
    phases: list[MissionPhase] | None = None

    @property
    def phase_list(self) -> list[MissionPhase]:
        """归一化阶段序列：显式 phases 优先，否则用单目标 objective。"""
        if self.phases:
            return self.phases
        return [
            MissionPhase(
                name="main", objective=self.objective, success_radius_m=self.success_radius_m
            )
        ]


class SustainmentSpec(BaseModel):
    """装备保障配置（M1 粗版：抢修组并发资源 + 平均修复时长；
    M3：备件库存与周期补给）。

    战斗受损与自然故障共用抢修队列；战毁（killed）不可修。
    spare_stock=None 表示备件无限（不启用供应链约束）；否则每次维修消耗
    1 件备件，库存耗尽后维修挂起，等待周期性补给投送。
    """

    repair_teams: dict[str, int] = {"red": 1, "blue": 1}
    # 受损后送修延迟（tick，确定性常量；后续改为分布抽样）
    repair_delay_ticks: int = 0
    # M3 备件供应链：各方初始库存（None=无限备件，保持旧行为）
    spare_stock: dict[str, int] | None = None
    # 周期补给：每隔 interval tick 各方收到 qty 件备件
    resupply_interval_ticks: int = Field(40, gt=0)
    resupply_qty: int = Field(4, ge=1)


class PlatformTypeSpec(BaseModel):
    """一类装备平台的性能参数（数值均为示例占位，等待真实数据资产替换）。"""

    sensor_range_m: float
    weapon_range_m: float
    pk: float = Field(..., ge=0.0, le=1.0, description="理想条件单次交火击毁概率")
    speed_m_per_tick: float
    # M1：交战节奏与可观测性
    reload_ticks: int = Field(2, ge=1, description="射击冷却（tick）")
    detect_prob: float = Field(0.85, ge=0.0, le=1.0, description="满近界发现目标概率")
    track_ttl_ticks: int = Field(3, ge=1, description="共享航迹过期时间（tick）")
    # M1：保障（平均故障间隔 / 平均修复时长，单位 tick；指数抽样）
    mtbf_ticks: float = Field(400.0, gt=0.0)
    mttr_ticks: float = Field(6.0, gt=0.0)
    meta: ParamMeta = ParamMeta()


class UnitSpec(BaseModel):
    unit_id: str
    type: str
    pos: tuple[float, float]
    # 编制层级标签（旅-营-连-排 自由文本链，用于态势图标注）
    org: str = ""
    heading_deg: float = 90.0  # 初始朝向，0=北(+y)，顺时针


class UnitTemplate(BaseModel):
    """成建制单位模板：按队形一次性展开 count 个同型单位（营/连级想定用）。"""

    type: str
    count: int = Field(..., ge=1)
    # 队形几何中心（展开后各单位位置的均值落在此处）
    anchor: tuple[float, float]
    heading_deg: float = 90.0  # 队形纵轴方向，0=北(+y)，顺时针
    org: str  # 编制名前缀，如 "重型合成1营·坦克1连"
    id_prefix: str  # unit_id 前缀，展开为 f"{prefix}{序号:02d}"
    layout: Literal["line", "column", "wedge"] = "line"
    spacing_along: float = 200.0  # 沿 heading 方向间距（米）
    spacing_lateral: float = 200.0  # 垂直 heading 方向间距（米）


class SideModifiers(BaseModel):
    """方别战斗乘子：既设阵地防御、训练水平等方别不对称要素的粗建模。

    乘到该方所有平台类型的装备基准值上（装备参数本身保持双方共用）。
    """

    pk_scale: float = Field(1.0, gt=0.0, description="该方单次交火毁伤概率乘子")
    detect_scale: float = Field(1.0, gt=0.0, description="该方发现概率乘子")
    speed_scale: float = Field(1.0, gt=0.0, description="该方机动速度乘子")


class Scenario(BaseModel):
    name: str
    mission: Mission = Mission()
    terrain: Terrain = Terrain()
    environment: Environment = Environment()
    platform_types: dict[str, PlatformTypeSpec]
    sustainment: SustainmentSpec = SustainmentSpec()
    # 方别不对称乘子（缺省键 = 全 1.0）；如蓝方既设防御火力加成
    side_modifiers: dict[SideName, SideModifiers] = {}
    forces: dict[SideName, list[UnitSpec]] = {"red": [], "blue": []}
    # 成建制模板：校验时展开并入 forces（展开后 forces 即为完整显式列表）
    force_templates: dict[SideName, list[UnitTemplate]] = {
        "red": [],
        "blue": [],
    }

    @model_validator(mode="after")
    def _expand_templates(self) -> "Scenario":
        if not any(self.force_templates.values()):
            return self
        from .orbat import expand_templates

        existing = {u.unit_id for side in ("red", "blue") for u in self.forces.get(side, [])}
        expanded = expand_templates(self.force_templates, set(self.platform_types))
        for side, new_units in expanded.items():
            for u in new_units:
                if u.unit_id in existing:
                    raise ValueError(f"单位 id 冲突（模板与显式列表）: {u.unit_id}")
                existing.add(u.unit_id)
            self.forces.setdefault(side, []).extend(new_units)
        self.force_templates = {"red": [], "blue": []}
        return self
