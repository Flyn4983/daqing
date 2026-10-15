"""C7-a0：气象 × 传感器衰减矩阵。

不同侦察/制导通道对同一气象条件的敏感度完全不同，这是合成作战中
"浓雾致盲光学、雷达照常工作""夜间红外优势"等战术差异的来源：

- 光学（目视/白光/电视制导）：雾、夜、烟幕、沙尘强衰减；
- 红外（热像）：夜间几乎不衰减，雾有一定穿透，烟幕仍有效；
- 雷达（微波）：昼夜/雾/烟幕几乎无影响，仅大雨有雨衰；
- 融合（fused）：多通道取优势。

数值为原型经验参数（量级参考公开军事气象-观测效能文献，待真实数据替换，
参数目录 S 系列条目），乘到传感器作用距离上。
"""

from __future__ import annotations

from ..data.schema import Environment

# 天气现象对三类通道的作用距离乘子
_WEATHER: dict[str, tuple[float, float, float]] = {
    # weather:        (光学, 红外, 雷达)
    "clear": (1.00, 0.95, 1.00),
    "overcast": (0.95, 0.95, 1.00),
    "rain": (0.65, 0.75, 0.95),
    "heavy_rain": (0.40, 0.55, 0.80),
    "snow": (0.55, 0.70, 0.92),
    "fog": (0.15, 0.45, 1.00),
    "heavy_fog": (0.06, 0.25, 1.00),
    "sand": (0.20, 0.40, 0.95),
    "haze": (0.70, 0.85, 1.00),
}

# 昼夜光照对三类通道的乘子（与天气相乘）
_ILLUM: dict[str, tuple[float, float, float]] = {
    "day": (1.00, 0.90, 1.00),
    "dusk": (0.55, 0.95, 1.00),
    "night": (0.20, 0.90, 1.00),
}

# 地面机动气象乘子（雨雪泥泞；沙漠/雪原地形本身在通行性中体现）
_GROUND_MOVE = {
    "clear": 1.0, "overcast": 1.0, "rain": 0.9, "heavy_rain": 0.75,
    "snow": 0.8, "fog": 0.95, "heavy_fog": 0.95, "sand": 0.85, "haze": 1.0,
}

# 空中架次禁飞气象（大风/暴雨/浓雾）
_GROUNDED_WEATHER = {"heavy_rain", "heavy_fog"}
_MAX_WIND_MPS = 12.0


def sensor_factors(env: Environment) -> dict[str, float]:
    """返回 optical/ir/radar/fused 四类通道的作用距离乘子。"""
    w = _WEATHER[env.weather]
    il = _ILLUM[env.illumination]
    optical = w[0] * il[0]
    ir = w[1] * il[1]
    radar = w[2] * il[2]
    return {"optical": optical, "ir": ir, "radar": radar, "fused": max(optical, ir, radar)}


def factor_for(env: Environment, kind: str) -> float:
    return sensor_factors(env)[kind]


def ground_move_factor(env: Environment) -> float:
    return _GROUND_MOVE[env.weather]


def air_grounded(env: Environment) -> bool:
    """当前气象是否不允许固定翼/旋翼/无人机架次起飞。"""
    return env.weather in _GROUNDED_WEATHER or env.wind_speed_mps >= _MAX_WIND_MPS
