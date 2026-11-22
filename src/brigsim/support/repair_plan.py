"""抢修方案生成（薄封装）：直接复用 plan.generate 的槽位/LLM/校验/溯源/docx 能力。

与"保障方案"(support_plan_v1.md) 的区别：
- 模板不同（repair_plan_v1.md，聚焦报损→评估→分级→抢拖救→修复归队的抢修作业闭环）；
- 方案名称标注为"抢修方案"，并记录 `plan_kind=repair` 与战损清单来源（仿真 / 人工 Word）；
- 数字、叙述、校验、溯源、docx 导出逻辑完全复用，零重复实现 → 权威数字零风险。

两种战损清单来源（仿真计算 / 人工填报 Word）在此汇聚：只要传入的 loss 结构是同构的
（battle_loss 的 {items, summary, ...}），本模块与 plan 层无需任何分支。
"""

from __future__ import annotations

from typing import Any

from . import battle_loss, plan as plan_mod
from .catalog import CATALOG

DEFAULT_REPAIR_TEMPLATE = "repair_plan_v1.md"

_SOURCE_LABEL = {
    "estimate": "仿真汇总估算",
    "events": "仿真事件流（逐件）",
    "manual_docx": "人工填报 Word 战损清单",
}


def source_label(loss: dict) -> str:
    """人类可读的战损来源，写入方案元信息，便于答辩说明数字出处。"""
    src = str(loss.get("source", ""))
    if src in _SOURCE_LABEL:
        return _SOURCE_LABEL[src]
    if loss.get("n_runs"):
        return "仿真多次推演"
    return "战损清单"


def generate(
    mission: dict,
    loss: dict,
    *,
    rates: dict | None = None,
    metrics: dict | None = None,
    scenario_info: dict | None = None,
    use_llm: bool = True,
    client=None,
    llm_params: dict | None = None,
    template: str = DEFAULT_REPAIR_TEMPLATE,
) -> dict:
    """生成抢修方案；返回结构与 plan.generate 完全一致，额外带 plan_kind/loss_source。"""
    result = plan_mod.generate(
        mission, loss,
        rates=rates, metrics=metrics, scenario_info=scenario_info,
        template=template, use_llm=use_llm, client=client, llm_params=llm_params,
    )
    result["plan_kind"] = "repair"
    result["loss_source"] = source_label(loss)
    result["loss_source_code"] = str(loss.get("source", ""))
    # 方案名称与正文标题里的名称改为"抢修方案"
    base_name = str(mission.get("task_name", "装备保障任务"))
    plan_name = f"{base_name}装备抢修方案"
    result["slots"]["plan_name"] = plan_name
    result["plan_name"] = plan_name
    # 重新填模板（plan_name 槽位已更新）并复核
    result["text"] = plan_mod.fill_template(plan_mod.read_template(template), result["slots"])
    result["validation"] = plan_mod.validate_document(result["text"], result["slots"])
    result["provenance"] = plan_mod.build_provenance(result["slots"], result["text"])
    return result


def save(plan: dict) -> str:
    """落盘复用 plan.save（同 store，不另起目录）。"""
    return plan_mod.save(plan)


def grade_legend() -> dict:
    """W 级口径（供前端提示/数据字典复用，不改权威口径）。"""
    return {
        "K": CATALOG["grade_names"]["3"],
        "F": CATALOG["grade_names"]["2"],
        "M": CATALOG["grade_names"]["1"],
        "N": CATALOG["grade_names"]["0"],
        "authoritative_attrition": "战损率只计 K 级；M/F 计入战斗损伤率但不计入战损率",
    }
