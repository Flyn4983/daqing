"""想定与参数的 Pydantic schema（M0 粗版，后续随数据资产扩展）。

约定：参数与代码分离；平台类型参数带来源元数据，支撑后续校正与版本化。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SideName = Literal["red", "blue"]


class ParamMeta(BaseModel):
    """参数来源溯源信息（M3 标定闭环用）。"""

    source: str = "example"
    version: str = "0.1"
    confidence: float = Field(0.5, ge=0.0, le=1.0)
    updated_at: str | None = None


class Terrain(BaseModel):
    # 方形战场边界（米）；FBM 噪声程序生成高程，M3 可替换为 GeoTIFF 实测数据
    size_m: tuple[float, float] = (10_000.0, 10_000.0)
    grid_n: int = 65
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


class Mission(BaseModel):
    duration_ticks: int = 120
    dt_seconds: int = 30
    objective: tuple[float, float] = (8_000.0, 5_000.0)
    success_radius_m: float = 300.0


class SustainmentSpec(BaseModel):
    """装备保障配置（M1 粗版：抢修组并发资源 + 平均修复时长）。

    战斗受损与自然故障共用抢修队列；战毁（killed）不可修。
    备件供应链在后续轮次扩展（接口预留）。
    """

    repair_teams: dict[str, int] = {"red": 1, "blue": 1}
    # 受损后送修延迟（tick，确定性常量；后续改为分布抽样）
    repair_delay_ticks: int = 0


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


class Scenario(BaseModel):
    name: str
    mission: Mission = Mission()
    terrain: Terrain = Terrain()
    environment: Environment = Environment()
    platform_types: dict[str, PlatformTypeSpec]
    sustainment: SustainmentSpec = SustainmentSpec()
    forces: dict[SideName, list[UnitSpec]]
