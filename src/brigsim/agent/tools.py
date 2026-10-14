"""智能体"函数调用"工具集：每个工具都返回真实数据，不编造。

数据底座（C3/C4 已入库产物）：
- models/surrogate_brigade_v1.joblib：C3 四率代理 MLP，单次预测毫秒级
  （由 5248 次 Warp GPU 蒙特卡洛推演数据训练，留出集平均 R²=0.88）；
- models/pareto_brigade_v1.csv：C4 NSGA-II 产出的 71 个 Pareto 非支配
  保障方案（含 knee/leanest/strongest 标签）；
- models/pareto_brigade_v1_validation.csv：代表方案 3×24 次蒙特卡洛
  仿真复验明细（代理-仿真误差，方案风险提示的证据来源）。

工具对应大模型 function-calling 的 schema：TOOLS 注册表给出名称、
业务说明与参数；engine 识别意图后按名调度，真实计时（毫秒）并留痕。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from ..optimize.nsga import evaluate_baseline
from ..optimize.problem import (
    DECISION_VARS,
    ScenarioContext,
    baseline_decisions,
    build_feature_matrix,
    resource_cost,
)
from ..surrogate.model import SurrogateModel

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MODEL_PATH = REPO_ROOT / "models" / "surrogate_brigade_v1.joblib"
DEFAULT_PARETO_PATH = REPO_ROOT / "models" / "pareto_brigade_v1.csv"
DEFAULT_VALIDATION_PATH = REPO_ROOT / "models" / "pareto_brigade_v1_validation.csv"

TICK_SECONDS = 30  # 1 tick = 30 秒（C1 口径，见 docs/data-interface.md)

KNOB_LABELS = {
    "red_repair_teams": "前沿抢修组",
    "red_spare_stock": "初始备件基数",
    "resupply_interval_ticks": "周期前送间隔",
    "resupply_qty": "每次前送批量",
}
PLAN_TAGS = {
    "knee": "折中推荐(knee)",
    "leanest": "资源最省(leanest)",
    "strongest": "保障最强(strongest)",
}

# 9 项代理预测指标的中文名与展示格式（pct=百分比，num=小数两位）
METRIC_META = {
    "success": ("任务完成率", "pct"),
    "attrition_red": ("红方战损率", "pct"),
    "attrition_blue": ("蓝方战损率", "pct"),
    "readiness_red": ("红方完好率", "pct"),
    "readiness_blue": ("蓝方完好率", "pct"),
    "repair_rate_red": ("红方修复率", "pct"),
    "repair_rate_blue": ("蓝方修复率", "pct"),
    "failure_rate_100h_red": ("红方故障率(次/百装备小时)", "num"),
    "failure_rate_100h_blue": ("蓝方故障率(次/百装备小时)", "num"),
}
KEY_METRICS = ["success", "attrition_red", "readiness_red", "repair_rate_red"]

# 函数调用注册表（C6 工具轨迹面板按此渲染名称与说明）
TOOLS: dict[str, dict] = {
    "predict_four_rates": {
        "desc": "四率代理秒级预测（输入 4 个保障旋钮，返回 GJB 口径四率+任务完成率）",
        "args": ["teams", "stock", "interval_ticks", "qty"],
    },
    "compare_with_baseline": {
        "desc": "自定义方案与 v3 标定基线及 Pareto 前沿对比（含被支配判定）",
        "args": ["teams", "stock", "interval_ticks", "qty"],
    },
    "query_pareto_front": {
        "desc": "查询 NSGA-II Pareto 保障方案集（knee/leanest/strongest 或成本最低 N 个）",
        "args": ["tag", "top_n"],
    },
    "generate_support_plan": {
        "desc": "一键生成结构化保障方案（配置+预期四率+基线对比+仿真复验+风险提示）",
        "args": ["tag"],
    },
    "get_rate_definitions": {
        "desc": "查询 GJB 口径四率与任务完成率的定义、分母口径与模型适用边界",
        "args": [],
    },
}


@lru_cache(maxsize=4)
def load_surrogate(path: str) -> SurrogateModel:
    return SurrogateModel.load(path)


class AgentToolkit:
    """智能体工具集：绑定一个代理模型、一张 Pareto 表与一个固定作战情景。"""

    def __init__(
        self,
        model_path: str | Path = DEFAULT_MODEL_PATH,
        pareto_path: str | Path = DEFAULT_PARETO_PATH,
        validation_path: str | Path = DEFAULT_VALIDATION_PATH,
        scenario_name: str = "example_brigade",
        duration_ticks: int = 59,
        ctx: ScenarioContext | None = None,
    ) -> None:
        self.model_path = str(model_path)
        self.scenario_name = scenario_name
        self.duration_ticks = int(duration_ticks)
        self.duration_minutes = self.duration_ticks * TICK_SECONDS / 60.0
        self.ctx = ctx or ScenarioContext.baseline()
        self.model = load_surrogate(self.model_path)
        self.pareto = pd.read_csv(pareto_path)
        self.validation = pd.read_csv(validation_path)
        self._baseline: pd.Series | None = None

    # ---- 基础：方案预测 ----

    def predict_four_rates(
        self, teams: int, stock: int, interval_ticks: int, qty: int
    ) -> dict:
        """代理模型预测自定义保障方案的 9 项指标 + 归一化资源成本。"""
        decisions = np.array([[teams, stock, interval_ticks, qty]], dtype=float)
        X = build_feature_matrix(decisions, self.ctx)
        pred = self.model.predict(X).iloc[0]
        cost = float(resource_cost(decisions, self.duration_ticks)[0])
        out = {m: float(pred[m]) for m in METRIC_META}
        out["cost"] = cost
        out["decisions"] = self._knob_dict(decisions[0])
        return out

    def compare_with_baseline(
        self, teams: int, stock: int, interval_ticks: int, qty: int
    ) -> dict:
        """自定义方案 vs v3 基线（差值）vs Pareto 前沿（是否被支配）。"""
        cur = self.predict_four_rates(teams, stock, interval_ticks, qty)
        base = self.baseline()
        deltas = {}
        for m in METRIC_META:
            deltas[m] = cur[m] - float(base[m])
        deltas["cost"] = cur["cost"] - float(base["cost"])
        knobs = [teams, stock, interval_ticks, qty]
        match = self.pareto[
            (self.pareto[DECISION_VARS] == pd.Series(knobs, index=DECISION_VARS)).all(axis=1)
        ]
        on_front = not match.empty
        rank_tag = str(match.iloc[0]["rank_tag"]) if on_front else ""
        # Pareto 中是否存在严格不差于本方案且至少一项更优的方案（支配判定）
        dom = self.pareto[
            (self.pareto["success"] >= cur["success"])
            & (self.pareto["attrition_red"] <= cur["attrition_red"])
            & (self.pareto["cost"] <= cur["cost"])
            & (
                (self.pareto["success"] > cur["success"])
                | (self.pareto["attrition_red"] < cur["attrition_red"])
                | (self.pareto["cost"] < cur["cost"] - 1e-9)
            )
        ]
        return {
            "plan": cur,
            "baseline": self._series_to_dict(base),
            "delta": deltas,
            "on_pareto_front": bool(on_front),
            "rank_tag": rank_tag,
            "dominated_by_count": int(len(dom)),
        }

    # ---- Pareto 方案集 ----

    def query_pareto_front(self, tag: str = "", top_n: int = 8) -> dict:
        """查询方案集：指定 knee/leanest/strongest 标签，或成本最低的 N 个。"""
        df = self.pareto
        if tag:
            if tag not in PLAN_TAGS:
                raise ValueError(f"未知方案标签 {tag}（可选 {sorted(PLAN_TAGS)}）")
            df = df[df["rank_tag"] == tag]
        cols = DECISION_VARS + KEY_METRICS[:2] + ["readiness_red", "cost", "rank_tag"]
        rows = [self._row_to_dict(r) for _, r in df[cols].head(top_n).iterrows()]
        qty_mode = int(self.pareto["resupply_qty"].mode().iloc[0])
        iv_med = float(self.pareto["resupply_interval_ticks"].median())
        return {
            "n_plans": int(len(self.pareto)),
            "tag": tag,
            "plans": rows,
            "front_finding": (
                f"{len(self.pareto)} 个非支配方案中，前送批量众数为 {qty_mode} 件，"
                f"前送间隔中位数 {iv_med:.0f} tick（{iv_med * TICK_SECONDS / 60:.0f} 分钟）"
            ),
        }

    def generate_support_plan(self, tag: str = "knee") -> dict:
        """一键生成结构化保障方案（C5 核心交付，C6 方案卡片直接渲染）。"""
        if tag not in PLAN_TAGS:
            raise ValueError(f"未知方案标签 {tag}（可选 {sorted(PLAN_TAGS)}）")
        row = self.pareto[self.pareto["rank_tag"] == tag].iloc[0]
        base = self.baseline()

        knobs = {k: int(row[k]) for k in DECISION_VARS}
        base_knobs = {k: int(base[k]) for k in DECISION_VARS}
        interval_min = knobs["resupply_interval_ticks"] * TICK_SECONDS / 60.0
        trips = self.duration_ticks // knobs["resupply_interval_ticks"]

        metrics, deltas = {}, {}
        for m in METRIC_META:
            metrics[m] = float(row[m])
            deltas[m] = metrics[m] - float(base[m])
        cost = float(row["cost"])
        deltas["cost"] = cost - float(base["cost"])

        # 仿真复验证据（C4 validation：真实蒙特卡洛，非代理）
        v = self.validation[self.validation["plan"] == tag]
        validation = [
            {
                "metric": str(r["metric"]),
                "surrogate": float(r["surrogate"]),
                "simulation": float(r["simulation"]),
                "abs_error": float(r["abs_error"]),
                "sim_std": float(r["sim_std"]),
            }
            for _, r in v.iterrows()
        ]
        return {
            "tag": tag,
            "tag_label": PLAN_TAGS[tag],
            "scenario": self.scenario_name,
            "duration_minutes": self.duration_minutes,
            "context": self.ctx.as_row(),
            "decisions": knobs,
            "baseline_decisions": base_knobs,
            "schedule": {
                "interval_minutes": interval_min,
                "estimated_trips": int(trips),
                "estimated_resupply_qty": int(trips * knobs["resupply_qty"]),
            },
            "metrics": metrics,
            "baseline_metrics": (
                {m: float(base[m]) for m in METRIC_META} | {"cost": float(base["cost"])}
            ),
            "delta": deltas,
            "cost": cost,
            "validation": validation,
            "n_pareto_plans": int(len(self.pareto)),
            "front_finding": self.query_pareto_front(top_n=1)["front_finding"],
        }

    def get_rate_definitions(self) -> dict:
        """GJB 口径四率定义（与 C1 experiment/four_rates.py 代码一致）。"""
        return {
            "完好率": "可用装备数（存活且未受损、未在修）/ 参战装备总数；另有时序平均完好率",
            "故障率": "自然故障次数 / 累计装备暴露量（可用单位·tick）；对外展示换算为次/百装备小时",
            "修复率": "修复完成数 / 应修装备数（应修=自然故障+战斗可修复受损并进入抢修通道者）",
            "战损率": "累计战斗毁伤数 / 参战装备数（战斗毁伤不可修复，区别于可修复受损）",
            "任务完成率": "顶层效能指标：是否在规定时限内完成预定任务（蒙特卡洛频率估计）",
            "适用边界": (
                f"模型时域为单场短促战斗（本想定 {self.duration_minutes:.0f} 分钟），"
                "MTTR 原型值只映射火线抢修；战役级周/月修理不在时域内。"
            ),
        }

    # ---- 公共 ----

    def baseline(self) -> pd.Series:
        if self._baseline is None:
            self._baseline = evaluate_baseline(self.model, self.duration_ticks, self.ctx)
        return self._baseline

    def context_info(self) -> dict:
        return {
            "scenario": self.scenario_name,
            "duration_ticks": self.duration_ticks,
            "duration_minutes": self.duration_minutes,
            "tick_seconds": TICK_SECONDS,
            "context": self.ctx.as_row(),
            "baseline_decisions": {
                k: int(v) for k, v in zip(DECISION_VARS, baseline_decisions()[0], strict=True)
            },
            "n_pareto_plans": int(len(self.pareto)),
        }

    @staticmethod
    def _knob_dict(arr: np.ndarray) -> dict:
        return {k: int(round(float(v))) for k, v in zip(DECISION_VARS, arr, strict=True)}

    @staticmethod
    def _row_to_dict(row: pd.Series) -> dict:
        out = {k: int(row[k]) for k in DECISION_VARS}
        for k in ("success", "attrition_red", "readiness_red", "cost"):
            out[k] = round(float(row[k]), 4)
        out["rank_tag"] = str(row["rank_tag"]) if isinstance(row["rank_tag"], str) else ""
        return out

    @staticmethod
    def _series_to_dict(s: pd.Series) -> dict:
        out = {m: float(s[m]) for m in METRIC_META}
        out["cost"] = float(s["cost"])
        return out
