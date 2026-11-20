"""智能体"函数调用"工具集：每个工具都返回真实数据，不编造。

数据底座（C3/C4 入库产物，C7-a3 起为 a2 实体化口径）：
- models/surrogate_brigade_v1.joblib：四率代理 MLP（a3 在 10 维新特征空间
  「7 保障决策 + 3 情景因子」上用 Mesa 多进程 CRN 数据集重训）；
- models/pareto_brigade_v1.csv：C4 NSGA-II 产出的 Pareto 非支配保障方案
  （含 knee/leanest/strongest 标签，文件名沿用 v1）；
- models/pareto_brigade_v1_validation.csv：代表方案蒙特卡洛仿真复验明细。

工具对应大模型 function-calling 的 schema：TOOLS 注册表给出名称、
业务说明与参数；engine 识别意图后按名调度，真实计时（毫秒）并留痕。
"""

from __future__ import annotations

import json
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
DEFAULT_SENS_PATH = REPO_ROOT / "src" / "brigsim" / "agent" / "web" / "data" / "sensitivity.json"

TICK_SECONDS = 30  # 1 tick = 30 秒（C1 口径，见 docs/data-interface.md)
N_PARTS = 4        # 器材部件种类（行走/动力/火控/武器）

# 7 个 a2 口径保障旋钮的中文名
KNOB_LABELS = {
    "red_repair_teams": "前沿抢修组(组)",
    "red_arv": "装甲抢救车(辆)",
    "red_supply_veh": "器材补给车(辆)",
    "carry_level": "携行档基数(档)",
    "operating_level": "运行档基数(档)",
    "wave_interval_ticks": "前送波次间隔(tick)",
    "wave_qty": "每波每部件(件)",
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
        "desc": "四率代理秒级预测（输入 7 个 a2 口径保障旋钮，返回 GJB 四率+任务完成率）",
        "args": ["teams", "arv", "supply_veh", "carry", "operating",
                 "interval_ticks", "wave_qty"],
    },
    "compare_with_baseline": {
        "desc": "自定义方案与 v4 标定基线及 Pareto 前沿对比（含被支配判定）",
        "args": ["teams", "arv", "supply_veh", "carry", "operating",
                 "interval_ticks", "wave_qty"],
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
    # --- C8 扩展：灵敏度 / 多方案对比 / 归因诊断 ---
    "get_sobol_indices": {
        "desc": "查询 Sobol 方差分解灵敏度指数（ST 总效应+一阶 S1），回答'什么因子影响最大'",
        "args": ["metric"],
    },
    "compare_plans": {
        "desc": "两套保障方案并排对比（knee vs leanest vs strongest，可指定任意两个标签）",
        "args": ["tag_a", "tag_b"],
    },
    "diagnose_four_rates": {
        "desc": "归因诊断：按 Sobol 灵敏度+代理预测+Pareto 建议解释当前四率水平的主要影响因子",
        "args": ["metric"],
    },
}

# 决策名 → 工具参数名
_ARG_TO_KNOB = {
    "teams": "red_repair_teams",
    "arv": "red_arv",
    "supply_veh": "red_supply_veh",
    "carry": "carry_level",
    "operating": "operating_level",
    "interval_ticks": "wave_interval_ticks",
    "wave_qty": "wave_qty",
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
        duration_ticks: int = 0,
        ctx: ScenarioContext | None = None,
    ) -> None:
        self.model_path = str(model_path)
        self.scenario_name = scenario_name
        if duration_ticks <= 0:
            from ..data.provider import YamlFileProvider

            duration_ticks = int(
                YamlFileProvider().load_scenario(scenario_name).mission.duration_ticks
            )
        self.duration_ticks = int(duration_ticks)
        self.duration_minutes = self.duration_ticks * TICK_SECONDS / 60.0
        self.ctx = ctx or ScenarioContext.baseline()
        self.model = load_surrogate(self.model_path)
        self.pareto = pd.read_csv(pareto_path)
        self.validation = pd.read_csv(validation_path)
        self._baseline: pd.Series | None = None
        # 扩展：Sobol 灵敏度数据（供 get_sobol_indices / diagnose_four_rates 使用）
        self._sensitivity: dict | None = None
        sens_path = Path(DEFAULT_SENS_PATH)
        if sens_path.exists():
            try:
                self._sensitivity = json.loads(sens_path.read_text(encoding="utf-8"))
            except Exception:
                pass

    # ---- 基础：方案预测 ----

    def _complete_decisions(self, overrides: dict[str, int | None]) -> np.ndarray:
        """把部分旋钮覆盖补成完整 7 维基线决策行。"""
        base = dict(zip(DECISION_VARS, baseline_decisions()[0], strict=True))
        for arg, val in overrides.items():
            if val is not None:
                base[_ARG_TO_KNOB[arg]] = float(val)
        return np.array([[base[k] for k in DECISION_VARS]], dtype=float)

    def predict_four_rates(
        self,
        teams: int | None = None,
        arv: int | None = None,
        supply_veh: int | None = None,
        carry: int | None = None,
        operating: int | None = None,
        interval_ticks: int | None = None,
        wave_qty: int | None = None,
    ) -> dict:
        """代理模型预测自定义保障方案的 9 项指标 + 归一化资源成本。"""
        overrides = {
            "teams": teams, "arv": arv, "supply_veh": supply_veh, "carry": carry,
            "operating": operating, "interval_ticks": interval_ticks, "wave_qty": wave_qty,
        }
        decisions = self._complete_decisions(overrides)
        X = build_feature_matrix(decisions, self.ctx)
        pred = self.model.predict(X).iloc[0]
        cost = float(resource_cost(decisions, self.duration_ticks)[0])
        out = {m: float(pred[m]) for m in METRIC_META}
        out["cost"] = cost
        out["decisions"] = self._knob_dict(decisions[0])
        return out

    def compare_with_baseline(
        self,
        teams: int | None = None,
        arv: int | None = None,
        supply_veh: int | None = None,
        carry: int | None = None,
        operating: int | None = None,
        interval_ticks: int | None = None,
        wave_qty: int | None = None,
    ) -> dict:
        """自定义方案 vs v4 基线（差值）vs Pareto 前沿（是否被支配）。"""
        cur = self.predict_four_rates(
            teams, arv, supply_veh, carry, operating, interval_ticks, wave_qty
        )
        base = self.baseline()
        deltas = {}
        for m in METRIC_META:
            deltas[m] = cur[m] - float(base[m])
        deltas["cost"] = cur["cost"] - float(base["cost"])
        knobs = cur["decisions"]
        match = self.pareto[
            (self.pareto[DECISION_VARS] == pd.Series(knobs)).all(axis=1)
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
        qty_mode = int(self.pareto["wave_qty"].mode().iloc[0])
        iv_med = float(self.pareto["wave_interval_ticks"].median())
        arv_mode = int(self.pareto["red_arv"].mode().iloc[0])
        return {
            "n_plans": int(len(self.pareto)),
            "tag": tag,
            "plans": rows,
            "front_finding": (
                f"{len(self.pareto)} 个非支配方案中，抢救车众数 {arv_mode} 辆、"
                f"每波每部件 {qty_mode} 件，波次间隔中位数 {iv_med:.0f} tick"
                f"（{iv_med * TICK_SECONDS / 60:.0f} 分钟）"
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
        interval_min = knobs["wave_interval_ticks"] * TICK_SECONDS / 60.0
        trips = self.duration_ticks // knobs["wave_interval_ticks"]

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
                "per_wave_parts": int(knobs["wave_qty"] * N_PARTS),
                "estimated_resupply_qty": int(trips * knobs["wave_qty"] * N_PARTS),
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

    # ---- C8 扩展：灵敏度 / 多方案对比 / 归因诊断 ----

    def _find_sens_metric(self, metric: str) -> dict | None:
        """在 sensitivity.json 中找一个 metric 条目。支持英文 key 和中文 label。"""
        if not self._sensitivity:
            return None
        metric = (metric or "").lower()
        for m in self._sensitivity.get("metrics", []):
            if m["key"] == metric or m["label"].startswith(metric):
                return m
        # 模糊匹配
        alias = {
            "完成率": "success", "成功率": "success", "success": "success",
            "红完好": "readiness_red", "红方完好率": "readiness_red",
            "红战损": "attrition_red", "红方战损率": "attrition_red",
            "蓝战损": "attrition_blue", "蓝方战损率": "attrition_blue",
            "蓝完好": "readiness_blue",
        }
        key = alias.get(metric)
        if key:
            for m in self._sensitivity.get("metrics", []):
                if m["key"] == key:
                    return m
        return None

    def get_sobol_indices(self, metric: str = "success") -> dict:
        """Sobol 方差分解灵敏度（ST 总效应 + 一阶 S1）。"""
        m = self._find_sens_metric(metric)
        if not m:
            return {
                "metric": metric, "found": False,
                "hint": "可用 metric：success / attrition_red / attrition_blue / readiness_red / readiness_blue",
            }
        rows = [
            {
                "param": r["param"], "label": r["label"],
                "S1": round(float(r["S1"]), 4),
                "ST": round(float(r["ST"]), 4),
                "rank": int(r["rank"]),
            }
            for r in m["rows"]
        ]
        return {
            "metric": m["key"], "label": m["label"],
            "method": self._sensitivity["method"],
            "found": True,
            "top3": m.get("top3", []),
            "rows": sorted(rows, key=lambda x: x["rank"]),
        }

    def compare_plans(self, tag_a: str = "knee", tag_b: str = "baseline") -> dict:
        """两套 Pareto 标签方案并排对比（含基线）。"""
        def _predict_tag(tag: str) -> tuple[dict, dict]:
            if tag == "baseline":
                cur = dict(self.baseline())
                cur["decisions"] = {k: int(v) for k, v in zip(DECISION_VARS, baseline_decisions()[0], strict=True)}
            else:
                p = self.query_pareto_front(tag=tag, top_n=1)
                if not p["plans"]:
                    return {"_error": f"tag '{tag}' not found"}, {}
                row = p["plans"][0]   # 直接是 DECISION_VARS 为 key 的行 dict
                decisions = {k: int(row.get(k, 0)) for k in DECISION_VARS}
                arr = np.array([[float(decisions[k]) for k in DECISION_VARS]])
                X = build_feature_matrix(arr, self.ctx)
                pred = self.model.predict(X).iloc[0]
                cur = {m: float(pred[m]) for m in METRIC_META}
                cur["cost"] = float(resource_cost(arr, self.duration_ticks)[0])
                cur["decisions"] = decisions
            base = dict(self.baseline())
            base["decisions"] = {k: int(v) for k, v in zip(DECISION_VARS, baseline_decisions()[0], strict=True)}
            return cur, base

        cur_a, _ = _predict_tag(tag_a)
        cur_b, _ = _predict_tag(tag_b)
        if "_error" in cur_a or "_error" in cur_b:
            return {"tag_a": tag_a, "tag_b": tag_b, "error": cur_a.get("_error") or cur_b.get("_error")}

        out = {"tag_a": tag_a, "tag_b": tag_b, "metrics": {}}
        for m in KEY_METRICS + ["cost"]:
            va = cur_a.get(m, float("nan"))
            vb = cur_b.get(m, float("nan"))
            out["metrics"][m] = {
                "name": METRIC_META.get(m, (m, "pct"))[0],
                "a": round(float(va), 4), "b": round(float(vb), 4),
                "delta": round(float(va) - float(vb), 4),
            }
        out["decisions_a"] = cur_a.get("decisions", {})
        out["decisions_b"] = cur_b.get("decisions", {})
        return out

    def diagnose_four_rates(self, metric: str = "success") -> dict:
        """归因诊断：Sobol 灵敏度 + 代理预测当前值 + 基线对比。"""
        sens = self.get_sobol_indices(metric)
        cur = self.predict_four_rates()          # knee/基线默认方案
        base = dict(self.baseline())
        if not sens.get("found"):
            return {"metric": metric, "found": False}

        top = sens["top3"]
        current_val = cur.get(metric, cur.get("cost"))
        baseline_val = base.get(metric, base.get("cost", float("nan")))
        delta = current_val - baseline_val
        return {
            "metric": sens["metric"],
            "label": sens["label"],
            "current": round(float(current_val), 4),
            "baseline": round(float(baseline_val), 4),
            "delta": round(float(delta), 4),
            "top_influencers": top,
            "interpretation": (
                f"当前{sens['label']}代理预测 {current_val*100:.1f}%，"
                f"较基线 {baseline_val*100:.1f}% {'高' if delta > 0 else '低'} {abs(delta)*100:.1f}pp；"
                f"Sobol ST 总效应最高的 3 个因子是 "
                + "、".join(f"{t['label']}({t['ST']*100:.1f}%)" for t in top)
                + "。"
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
