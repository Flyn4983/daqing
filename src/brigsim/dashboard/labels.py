"""C6 大屏中文标签（与 C1 GJB 口径、C3/C4/C5 同源；独立常量，不反向依赖 agent）。"""

from __future__ import annotations

SCHEMA_PREFIX = "brigsim-dash"
SCHEMA_VERSION = 1
TICK_SECONDS = 30

# 代理模型 9 目标 + 任务完成率：key → (中文名, 展示类型)
# kind: "pct" = 概率/比率（0–1，展示为百分比）；"num" = 一般数值（故障率/100 装备小时）
METRIC_LABELS: dict[str, tuple[str, str]] = {
    "success": ("任务完成率", "pct"),
    "attrition_red": ("红方战损率", "pct"),
    "attrition_blue": ("蓝方战损率", "pct"),
    "readiness_red": ("红方完好率", "pct"),
    "readiness_blue": ("蓝方完好率", "pct"),
    "repair_rate_red": ("红方修复率", "pct"),
    "repair_rate_blue": ("蓝方修复率", "pct"),
    "failure_rate_100h_red": ("红方故障率(/百装备时)", "num"),
    "failure_rate_100h_blue": ("蓝方故障率(/百装备时)", "num"),
}

# 灵敏度 5 指标（Sobol 输出口径）
SENS_METRIC_LABELS: dict[str, str] = {
    "success": "任务完成率",
    "attrition_red": "红方战损率",
    "readiness_red": "红方完好率",
    "attrition_blue": "蓝方战损率",
    "readiness_blue": "蓝方完好率",
}

# Sobol 8 因子中文名
PARAM_LABELS: dict[str, str] = {
    "red_speed": "红方机动速度",
    "red_pk": "红方命中毁伤",
    "red_detect": "红方侦察探测",
    "blue_speed": "蓝方机动速度",
    "blue_pk": "蓝方命中毁伤",
    "blue_detect": "蓝方侦察探测",
    "mtbf": "装备可靠性 MTBF",
    "visibility": "气象能见度",
}

# 保障 4 旋钮（C4 决策变量，C6 方案详情用）
KNOB_LABELS: dict[str, str] = {
    "red_repair_teams": "抢修组数(组)",
    "red_spare_stock": "初始备件(件)",
    "resupply_interval_ticks": "前送间隔(tick)",
    "resupply_qty": "每次前送(件)",
}

# Pareto 三标签
PLAN_LABELS: dict[str, str] = {
    "knee": "折中推荐",
    "leanest": "资源最省",
    "strongest": "保障最强",
}

# 大屏两方案
PLAN_SCENARIO_LABELS: dict[str, str] = {
    "knee": "knee 推荐保障方案",
    "baseline": "现状保障基线",
}

# 四率时序图可选指标（红/蓝双方，键→中文名）
TIMESERIES_METRICS: dict[str, str] = {
    "availability": "完好率",
    "attrition": "战损率",
    "repair_rate": "修复率",
    "failure_rate": "故障率",
}
