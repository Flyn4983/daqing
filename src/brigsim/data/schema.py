"""想定与参数的 Pydantic schema（M0 粗版，后续随数据资产扩展）。

约定：参数与代码分离；平台类型参数带来源元数据，支撑后续校正与版本化。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

SideName = Literal["red", "blue"]

# C7-a0：兵种角色、火力模式、传感器类型
RoleName = Literal[
    "assault",       # 突击：坦克/步战/反坦克组
    "artillery",     # 间瞄炮兵（榴弹炮/迫击炮/火箭炮）
    "missile",       # 超视距导弹（红箭-10，需侦察指引）
    "air_defense",   # 防空导弹/高炮
    "radar_air",     # 对空警戒雷达
    "radar_cb",      # 反炮兵定位雷达
    "recon",         # 装甲侦察（雷达+光电）
    "ew",            # 电子对抗（雷达/通信干扰）
    "c2",            # 指挥通信节点
    "engineer",      # 工兵（破障/架桥）
    "support",       # 保障车辆（抢救/抢修/补给/发烟）
]
FireModeName = Literal["direct", "indirect", "blos", "air_defense", "support"]
SensorKindName = Literal["optical", "ir", "radar", "fused"]
JamModeName = Literal["radar", "comms", "both"]
WeatherName = Literal[
    "clear", "overcast", "rain", "heavy_rain", "snow", "fog", "heavy_fog", "sand", "haze"
]
IlluminationName = Literal["day", "dusk", "night"]


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
    # C7-a0：多维气象（经 combat/weather.py 的传感器衰减矩阵差异化作用于光学/红外/雷达）
    weather: WeatherName = "clear"
    illumination: IlluminationName = "day"  # 昼夜光照
    # 兼容旧字段：不直接使用，传感器乘子改由 weather+illumination 计算
    visibility_factor: float = 1.0
    wind_speed_mps: float = 0.0     # 风速：影响无人机起飞、烟幕扩散
    wind_dir_deg: float = 0.0       # 风向（气象方位角，度）


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
    repair_station 给出各方修理所 [x, y]（M3 后送物理位移）：在修单位
    向本方修理所机动（后送脱离接触），修复后按教条再前出；None 时在修
    单位原地不动（旧行为）。
    """

    repair_teams: dict[str, int] = {"red": 1, "blue": 1}
    # 受损后送修延迟（tick，确定性常量；后续改为分布抽样）
    repair_delay_ticks: int = 0
    # M3 备件供应链：各方初始库存（None=无限备件，保持旧行为）
    spare_stock: dict[str, int] | None = None
    # 周期补给：每隔 interval tick 各方收到 qty 件备件
    resupply_interval_ticks: int = Field(40, gt=0)
    resupply_qty: int = Field(4, ge=1)
    # M3 修理所位置（各方 [x_m, y_m]）；None=在修单位原地不动
    repair_station: dict[str, list[float]] | None = None


class PlatformTypeSpec(BaseModel):
    """一类装备平台的性能参数（数值均为示例占位，等待真实数据资产替换）。

    C7-a0 新增合成兵种字段（均有默认值，旧想定零改动可加载）：
    role/fire_mode/sensor_kind 决定平台在战场体系中的行为类别；
    间瞄/导弹/防空走战斗支援系统（combat/systems.py）而非普通直瞄交火。
    """

    sensor_range_m: float
    weapon_range_m: float
    pk: float = Field(..., ge=0.0, le=1.0, description="理想条件单次交火击毁概率")
    speed_m_per_tick: float
    # M1：交战节奏与可观测性
    reload_ticks: int = Field(2, ge=1, description="射击冷却（tick）")
    detect_prob: float = Field(0.85, ge=0.0, le=1.0, description="满近界发现概率")
    track_ttl_ticks: int = Field(3, ge=1, description="共享航迹过期时间（tick）")
    # M1：保障（平均故障间隔 / 平均修复时长，单位 tick；指数抽样）
    mtbf_ticks: float = Field(400.0, gt=0.0)
    mttr_ticks: float = Field(6.0, gt=0.0)
    # ---- C7-a0：合成兵种体系 ----
    role: RoleName = "assault"
    fire_mode: FireModeName = "direct"
    sensor_kind: SensorKindName = "optical"
    splash_radius_m: float = Field(0.0, ge=0.0, description="间瞄面杀伤半径（米）")
    call_fire_delay_ticks: int = Field(0, ge=0, description="呼叫火力到弹着延迟（tick）")
    emplace_ticks: int = Field(0, ge=0, description="占领阵地展开时间（tick）")
    ammo_basic_load: int | None = Field(None, description="弹药基数（发）；None=不限制")
    coverage_radius_m: float = Field(0.0, ge=0.0, description="雷达/防空/电抗/C2 作用半径（米）")
    emission_duty: float = Field(1.0, ge=0.0, le=1.0, description="雷达间歇开机占空比")
    jam_strength: float = Field(0.0, ge=0.0, le=1.0, description="电抗强度（0-1）")
    jam_mode: JamModeName = "both"
    aura_bonus: float = Field(0.0, ge=0.0, description="C2 光环对感知/毁伤概率的加成")
    meta: ParamMeta = ParamMeta()


class UnitSpec(BaseModel):
    unit_id: str
    type: str
    pos: tuple[float, float]
    # 编制层级标签（旅-营-连-排 自由文本链，用于态势图标注）
    org: str = ""
    heading_deg: float = 90.0  # 初始朝向，0=北(+y)，顺时针
    # C7-a0：既设掩体（被发现与被毁概率降低）；假目标（吸引敌方精确弹药）
    dug_in: bool = False
    decoy: bool = False


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
    dug_in: bool = False  # 整建制占领既设阵地（蓝方防御用）
    decoy: bool = False


class SideModifiers(BaseModel):
    """方别战斗乘子：既设阵地防御、训练水平等方别不对称要素的粗建模。

    乘到该方所有平台类型的装备基准值上（装备参数本身保持双方共用）。
    """

    pk_scale: float = Field(1.0, gt=0.0, description="该方单次交火毁伤概率乘子")
    detect_scale: float = Field(1.0, gt=0.0, description="该方发现概率乘子")
    speed_scale: float = Field(1.0, gt=0.0, description="该方机动速度乘子")


class ObstacleSpec(BaseModel):
    """障碍区（蓝方防御体系，矩形轴对齐）：雷场/反坦克壕/三角锥。

    未开辟通路时：雷场减速并有概率触雷损伤；反坦克壕近乎阻断，
    必须由 role=engineer 的破障车在作业距离内累计 breach_ticks 开辟。
    """

    kind: Literal["minefield", "anti_tank_ditch", "dragons_teeth"]
    center: tuple[float, float]
    size: tuple[float, float]  # [x 方向全宽, y 方向全宽]（米）
    breach_ticks: int = Field(8, ge=1)


class SmokeOrder(BaseModel):
    """发烟命令：发烟车（unit_id）抵达 at 附近后，在 start_tick 起施放持续烟幕。"""

    unit_id: str
    at: tuple[float, float]
    radius_m: float = 500.0
    start_tick: int = 0
    duration_ticks: int = 6


class BattlefieldSpec(BaseModel):
    """战场人工环境层：障碍区 + 预有发烟命令（烟幕随发烟车到位与时间动态生效）。"""

    obstacles: list[ObstacleSpec] = []
    smoke_orders: list[SmokeOrder] = []


class AirPackage(BaseModel):
    """空中架次（轻量时序实体，非地面平台）：起飞-进入-巡弋-打击/侦察-返航。

    kind：uav 侦察校射（sensor_range_m>0，持续产出共享航迹）；
    loitering 巡飞弹（到点后按 seek_radius_m 选高价值目标一击）；
    helo 武装直升机（战位上对装甲多发射击）。
    """

    side: SideName
    kind: Literal["uav", "loitering", "helo"]
    launch_tick: int = Field(0, ge=0)
    waypoints: list[tuple[float, float]]  # 最后一个航点为战位/巡弋中心
    altitude_m: float = 1500.0
    loiter_ticks: int = Field(10, ge=1)
    seek_radius_m: float = 1500.0
    weapon_pk: float = Field(0.0, ge=0.0, le=1.0)
    weapon_count: int = Field(0, ge=0)
    sensor_range_m: float = 0.0
    sensor_kind: SensorKindName = "fused"
    label: str = ""


class Scenario(BaseModel):
    name: str
    mission: Mission = Mission()
    terrain: Terrain = Terrain()
    environment: Environment = Environment()
    platform_types: dict[str, PlatformTypeSpec]
    sustainment: SustainmentSpec = SustainmentSpec()
    # C7-a0：战场障碍/烟幕层、空中架次
    battlefield: BattlefieldSpec = BattlefieldSpec()
    air_packages: list[AirPackage] = []
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
