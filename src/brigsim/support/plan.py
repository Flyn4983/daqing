"""保障方案生成：模板槽位确定性填充 + 大模型只写叙述 + 数字一致性校验。

核心原则（对应《与合成旅保障对齐_需求与改造分析_v1.md》§4.5）：
- **数字不由 LLM 生成**：数值槽位全部来自战损清单 / 推演汇总 / 录入数据。
- **LLM 只写叙述**：情况判断、决心、力量区分、措施等文字由本地 Qwen3.8-27B 生成；
  失败自动降级为模板默认叙述（auto 模式可离线演示）。
- **可替换模板**：正式模板 `.md` 用 `{{slot_id}}` 标记槽位；替换模板只需保持槽位 ID
  与 `catalog.SLOT_DICT` 一致。模板放在 `templates/support/`，首次运行自动生成样例。

落盘 `outputs/support/plans/plan_<id>.json` + `.md`。
"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from .battle_loss import parts_needed
from .catalog import SLOT_DICT
from .mission_input import summary as mission_summary

_REPO = Path(__file__).resolve().parents[3]
_TEMPLATE_DIR = _REPO / "templates" / "support"
_TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
_STORE = _REPO / "outputs" / "support" / "plans"
_STORE.mkdir(parents=True, exist_ok=True)

DEFAULT_TEMPLATE = "support_plan_v1.md"

# 十章制式方案样例模板（用户可直接替换为正式模板，只需保留 {{slot_id}}）
_TEMPLATE_TEXT = """**编号：{{plan_id}}　　{{plan_name}}**

**一、情况判断**
（一）敌情：{{enemy_situation}}。敌主要火力为 {{enemy_fire}}，我装备面临 {{threat_type}} 威胁。
　　敌方兵力编成：{{enemy_strength_desc}}。
（二）我情：本次任务参战装备 {{roster_total}} 台（套），编成为 {{roster_by_category}}。战备等级 {{readiness_level}}。
　　红蓝兵力对比：{{force_balance_desc}}。
（三）地形与天候：{{terrain_desc}}；{{weather_desc}}；能见度 {{visibility_desc}}。
（四）上级意图与保障要求：{{superior_intent}}。

**二、保障任务与决心**
（一）保障任务：确保任务期间装备完好率不低于 {{target_readiness}}，任务完成率不低于 {{target_success}}，战损装备抢救抢修及时率不低于 {{target_repair_timeliness}}。
（二）保障原则：模块化编组、分级保障、靠前抢修、精准补给、留有余地。
（三）保障企图：以 {{repair_teams}} 个抢修组、{{arv_count}} 台抢救车、{{supply_veh}} 台器材车编成保障群，配置于 {{support_area}}，对 M 级现地抢修、F 级拖救后送、K 级报废补充。

**三、战损预计与保障需求测算**
（一）战损预计：依据 {{n_runs}} 次蒙特卡洛推演，预计我方战毁 {{pred_k}} 台（战损率 {{pred_attrition}}）、机动受损 {{pred_m}} 台、火力受损 {{pred_f}} 台；战斗损伤率 {{pred_damage_rate}}，任务完成率 {{pred_success}}，完好率 {{pred_readiness}}。
（二）器材需求：行走总成 {{need_mobility}} 件、动力总成 {{need_powerpack}} 件、火控系统 {{need_fire_control}} 件、武器系统 {{need_weapon}} 件，携行/运行/基地三级储备分别为 {{carry_stock}} / {{operating_stock}} / {{depot_stock}}。
（三）弹药需求：{{ammo_by_class}}，前送基数 {{ammo_wave_qty}}。
（四）油料需求：预计消耗 {{fuel_consumption_l}} 升，储备 {{fuel_stock_l}} 升，油罐车 {{tanker_count}} 台，前送 {{fuel_resupply_l}} 升。

**四、保障力量编组与部署**
（一）保障指挥所：{{cp_location}}，组成 {{cp_composition}}。
（二）抢修组：{{repair_teams}} 组，任务区分 {{repair_task_split}}。
（三）抢救组：{{arv_count}} 台，配置 {{arv_location}}。
（四）器材补给组：{{supply_veh}} 台，配置 {{supply_location}}。
（五）弹药/油料保障组：配置 {{ammo_fuel_location}}。

**五、抢救抢修计划**
（一）分级维修：M 级由伴随抢修组 {{m_repair_rule}}；F 级由抢救组拖救至 {{f_repair_site}}；K 级判定报废、{{k_disposal}}。
（二）抢修顺序：先 {{repair_priority_1}}，后 {{repair_priority_2}}。
（三）预计修复 {{est_repaired}} 台，保障持续战力再生。

**六、器材、弹药、油料补给计划**
（一）器材前送：每 {{wave_interval_min}} 分钟一波，每波 {{wave_qty}} 件，前送半径 {{dist_radius_m}} 米。
（二）弹药前送：{{ammo_delivery_plan}}。
（三）油料补给：{{fuel_delivery_plan}}。

**七、装备后送计划**
后送路线 {{evac_routes}}；后送工具 {{evac_assets}}；后送顺序 {{evac_priority}}；后送时限 {{evac_timeline}}。

**八、防卫、通信与运输保障**
（一）防卫：{{defense_plan}}。（二）通信：{{comm_plan}}。（三）运输：{{transport_plan}}。

**九、实施步骤与报告时机**
（一）{{phase_1}}；（二）{{phase_2}}；（三）{{phase_3}}。
报告时机：{{report_schedule}}；情况变化时按 {{revision_rule}} 修订。

**十、附表**
{{table_loss}}
{{table_spares}}
{{table_org}}
{{map_deploy}}
{{table_schedule}}

---
*本方案数字由仿真推演与战损清单真实计算；叙述文字由本地大模型（Qwen3.8-27B）依据数据与模板生成，经数字一致性校验。*
"""

# ---- 叙述槽位默认文字（LLM 不可用时降级，绝不编造数字）----
_NARRATIVE_FALLBACK: dict[str, str] = {
    "enemy_situation": "敌以空地火力实施联合打击，重点压制我突击与保障节点",
    "enemy_fire": "远程火箭炮、反坦克导弹与空中精确弹药",
    "threat_type": "机动受损与火力受损并存的复合战损",
    "superior_intent": "依托旅级保障力量模块化编组，保持持续作战能力",
    "support_area": "旅支援地域靠近主要补给线、便于抢救车辆进出",
    "cp_location": "旅保障指挥所位于支援地域前缘、便于靠前指挥",
    "cp_composition": "由保障部首长、装备参谋及修理、补给、运输各组组长组成",
    "repair_task_split": "一组专注机动受损抢修，二组负责火控与武器系统修复",
    "arv_location": "抢救组配置于主要机动路线侧翼、便于快速拖救",
    "supply_location": "器材补给组配置于保障群中枢、靠近前送路线",
    "ammo_fuel_location": "弹药与油料保障组配置于补给线节点、避开敌火力覆盖区",
    "m_repair_rule": "就地抢修、限时归队",
    "f_repair_site": "营级修理点",
    "k_disposal": "组织残骸回收与补充申请",
    "repair_priority_1": "突击力量主战装备",
    "repair_priority_2": "火力与指挥节点装备",
    "ammo_delivery_plan": "按波次前送，优先保障一线消耗单位",
    "fuel_delivery_plan": "依托油料补给车定点加注与伴随补给相结合",
    "evac_routes": "经保障群集结地域至后方修理所的多条机动路线",
    "evac_assets": "装甲抢救车与运输车",
    "evac_priority": "先可修复的重型装备、后一般受损装备",
    "evac_timeline": "按抢修窗口梯次后送",
    "defense_plan": "保障群构成环形防卫，防空与警戒力量伴随掩护",
    "comm_plan": "依托旅级通信网建立保障专用频道，保持不间断联络",
    "transport_plan": "统一调配运输力量，前送与后送分批错峰实施",
    "phase_1": "受领任务后完成装备清点、战损预计与保障决心",
    "phase_2": "战斗中按分级处置规则组织抢救抢修与精准补给",
    "phase_3": "阶段结束汇总战损与保障成效，修订后续方案",
    "report_schedule": "每个阶段结束及战损突变时即时报告",
    "revision_rule": "上级意图、敌情或战损态势显著变化时修订",
    "table_schedule": "按阶段列出抢救、抢修、补给、后送任务时序（见平台保障过程视图）",
}

_LLM_SLOTS = list(_NARRATIVE_FALLBACK)
# 分段生成，避免单次请求过长；同时降低 ARM CPU 上 27B 模型的显存/内存压力。
_LLM_GROUPS: list[tuple[str, list[str]]] = [
    ("情况判断与上级意图", ["enemy_situation", "enemy_fire", "threat_type", "superior_intent"]),
    ("保障决心与力量部署", ["support_area", "cp_location", "cp_composition", "repair_task_split",
                       "arv_location", "supply_location", "ammo_fuel_location"]),
    ("抢修与补给后送", ["m_repair_rule", "f_repair_site", "k_disposal", "repair_priority_1",
                   "repair_priority_2", "ammo_delivery_plan", "fuel_delivery_plan",
                   "evac_routes", "evac_assets", "evac_priority", "evac_timeline"]),
    ("防卫通信与实施步骤", ["defense_plan", "comm_plan", "transport_plan", "phase_1", "phase_2",
                      "phase_3", "report_schedule", "revision_rule", "table_schedule"]),
]

_SLOT_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")
# LLM 叙述里禁止出现任何阿拉伯数字（中文数字如"三个营"允许）
# 注意：不能用 \b 边界——中文字符属于 \w，"3个营" 会被误判为无数字。
_NUM_RE = re.compile(r"\d")


def ensure_template(name: str = DEFAULT_TEMPLATE) -> Path:
    f = _TEMPLATE_DIR / name
    if not f.is_file():
        f.write_text(_TEMPLATE_TEXT, encoding="utf-8")
    return f


def list_templates() -> list[dict]:
    ensure_template()
    return [{"id": f.name, "name": f.stem, "size": f.stat().st_size}
            for f in sorted(_TEMPLATE_DIR.glob("*.md"))]


def read_template(name: str = DEFAULT_TEMPLATE) -> str:
    return ensure_template(name).read_text(encoding="utf-8")


def save_template(name: str, text: str) -> str:
    """写入自定义模板；校验槽位 ID 是否都在字典中。"""
    if not name.endswith(".md"):
        name += ".md"
    slots = set(_SLOT_RE.findall(text))
    unknown = sorted(slots - set(SLOT_DICT))
    if unknown:
        raise ValueError(f"模板含未知槽位：{unknown}（请对照 SLOT_DICT）")
    f = _TEMPLATE_DIR / name
    f.write_text(text, encoding="utf-8")
    return f.name


# ---------------- 槽位填充 ----------------

def _pct(v: float) -> str:
    return f"{float(v) * 100:.1f}%"


def build_slots(
    mission: dict,
    loss: dict,
    *,
    rates: dict | None = None,
    metrics: dict | None = None,
    scenario_info: dict | None = None,
    plan_id: str = "",
) -> dict:
    """构造全部槽位值：数字来自清单/录入（确定性），叙述先填默认文字。"""
    ms = mission_summary(mission)
    s = loss.get("summary") or {}
    g = s.get("by_grade") or {}
    r = rates or {}
    k = int(g.get("K", 0)); m = int(g.get("M", 0)); f = int(g.get("F", 0))
    need = parts_needed(loss)
    info = scenario_info or {}
    # 任务完成率：优先用推演汇总里的 success_rate，否则按战毁比例估
    est = metrics or {}
    succ = est.get("success_rate", est.get("mission_success", None))
    if succ is None:
        n = int(r.get("n_roster") or s.get("roster_size") or 0)
        succ = 1 - (k / n if n else 0)
    succ = float(succ)
    if succ > 1:
        succ /= 100.0

    slots: dict[str, Any] = {
        "plan_id": plan_id or f"PQ-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}",
        "plan_name": f"{mission.get('task_name', '装备保障任务')}保障方案",
        "task_id": mission.get("task_id", ""),
        "roster_total": ms["roster_total"],
        "roster_by_category": ms["roster_by_category"],
        "readiness_level": ms["readiness_level"],
        "enemy_strength_desc": (
            f"地面装备 {ms['blue_total']} 台（套）、空中架次 {ms['blue_air']} 架，"
            f"交火效能 {ms['blue_pk_scale']:.2f} 倍（依录入战损清单核定）"
            if ms.get("forces_configured") else "按基础想定编成，未作自定义录入"),
        "force_balance_desc": (
            f"红方地面 {ms['red_total']} 台 / 空中 {ms['red_air']} 架，"
            f"蓝方地面 {ms['blue_total']} 台 / 空中 {ms['blue_air']} 架，"
            f"交火效能比 {ms['red_pk_scale']:.2f} : {ms['blue_pk_scale']:.2f}"
            if ms.get("forces_configured") else "按基础想定双方编成对比"),
        "terrain_desc": info.get("terrain", "山地丘陵地形"),
        "weather_desc": info.get("weather", "天气条件按想定设定"),
        "visibility_desc": info.get("visibility", "能见度按想定设定"),
        "target_readiness": (mission.get("targets") or {}).get("readiness", "90%"),
        "target_success": (mission.get("targets") or {}).get("success", "85%"),
        "target_repair_timeliness": (mission.get("targets") or {}).get("repair_timeliness", "80%"),
        "repair_teams": ms["repair_teams"],
        "arv_count": ms["arv_count"],
        "supply_veh": ms["supply_veh"],
        "n_runs": loss.get("n_runs", 0),
        "pred_k": k,
        "pred_m": m,
        "pred_f": f,
        "pred_attrition": _pct(r.get("attrition", 0)),
        "pred_damage_rate": _pct(r.get("damage_rate", 0)),
        "pred_success": _pct(succ),
        "pred_readiness": _pct(r.get("readiness", 0)),
        "need_mobility": need.get("mobility", 0),
        "need_powerpack": need.get("powerpack", 0),
        "need_fire_control": need.get("fire_control", 0),
        "need_weapon": need.get("weapon", 0),
        "carry_stock": ms["carry_stock"],
        "operating_stock": ms["operating_stock"],
        "depot_stock": ms["depot_stock"],
        "ammo_by_class": ms["ammo_by_class"],
        "ammo_wave_qty": ms["ammo_wave_qty"],
        "fuel_consumption_l": ms["fuel_consumption_l"],
        "fuel_stock_l": ms["fuel_stock_l"],
        "tanker_count": ms["tanker_count"],
        "fuel_resupply_l": ms["fuel_resupply_l"],
        "est_repaired": m + f,
        "wave_interval_min": ms["wave_interval_min"],
        "wave_qty": ms["wave_qty"],
        "dist_radius_m": ms["dist_radius_m"],
        "table_loss": _table_loss(loss),
        "table_spares": _table_spares(need, ms),
        "table_org": _table_org(ms),
        "map_deploy": "（部署要图：见平台三维态势视图，随方案导出）",
    }
    slots.update({key: _NARRATIVE_FALLBACK[key] for key in _LLM_SLOTS})
    return slots


def _table_loss(loss: dict) -> str:
    s = loss.get("summary") or {}
    g = s.get("by_grade") or {}
    names = {"K": "战毁", "M": "机动受损", "F": "火力受损"}
    rows = [f"| {code} | {names.get(code, code)} | {int(g.get(code, 0))} |"
            for code in ("K", "M", "F")]
    return ("**附表1　战损统计表**\n\n| 等级 | 判定 | 数量 |\n|---|---|---|\n"
            + "\n".join(rows) + f"\n| 合计 | — | {sum(int(g.get(c, 0)) for c in ('K', 'M', 'F'))} |")


def _table_spares(need: dict, ms: dict) -> str:
    labels = {"mobility": "行走总成", "powerpack": "动力总成",
              "fire_control": "火控系统", "weapon": "武器系统"}
    rows = [f"| {labels[k]} | {need.get(k, 0)} | {ms['carry_stock']} / {ms['operating_stock']} / {ms['depot_stock']} |"
            for k in labels]
    return ("**附表2　器材需求表**\n\n| 器材类别 | 预计需求 | 携行/运行/基地储备 |\n|---|---|---|\n"
            + "\n".join(rows))


def _table_org(ms: dict) -> str:
    return ("**附表3　保障力量编成表**\n\n| 力量 | 数量 | 主要任务 |\n|---|---|---|\n"
            f"| 抢修组 | {ms['repair_teams']} | M/F 级现地抢修 |\n"
            f"| 抢救车 | {ms['arv_count']} | F 级拖救后送 |\n"
            f"| 器材补给车 | {ms['supply_veh']} | 器材前送 |")


# ---------------- LLM 叙述 ----------------

def _llm_system() -> str:
    return (
        "你是合成旅装备保障参谋。只根据给定的战损清单统计与任务数据，撰写保障方案中的叙述性文字。"
        "严格要求：1) 禁止输出任何阿拉伯数字或百分比，数量一律由模板槽位填充，不得自己编写数字；"
        "2) 不得虚构敌情番号、具体时间、具体地名；3) 只输出合法 JSON 对象，"
        "键名为指定槽位 ID，键值为该槽位的一句话文字（不超过 60 字）；"
        "4) 语气为军用公文体，简洁、可执行。"
    )


def _llm_user(group_name: str, keys: list[str], context: dict) -> str:
    return (
        f"【撰写段落】{group_name}\n"
        f"【任务与战损事实】\n{json.dumps(context, ensure_ascii=False, indent=1)}\n"
        f"【必须返回的 JSON 键（每个键写一句话，不得含数字）】\n"
        + ", ".join(keys)
    )


def _extract_json(text: str) -> dict:
    """从可能带 markdown 围栏的响应中提取 JSON 对象。"""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        obj = json.loads(t[start:end + 1])
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        return {}


def generate_narratives(slots: dict, *, client=None, use_llm: bool = True,
                        llm_params: dict | None = None) -> dict:
    """分段调用本地 LLM 生成叙述槽位；失败逐段降级为默认文字。

    P1-3：`llm_params` 可覆盖 temperature / max_tokens / retries / group_size，
    便于 27B CPU 部署下权衡速度与质量。缺省行为与原来完全一致。
    """
    lp = dict(llm_params or {})
    temperature = float(lp.get("temperature", 0.25))
    max_tokens = int(lp.get("max_tokens", 900))
    retries = max(0, min(3, int(lp.get("retries", 1))))
    group_size = max(1, min(len(_LLM_GROUPS), int(lp.get("group_size", len(_LLM_GROUPS)))))
    groups = _LLM_GROUPS[:group_size]
    # group_size < 全部分段时，其余槽位保持默认文字（不丢失槽位）
    result: dict[str, str] = {}
    meta: dict = {"attempted": False, "groups_ok": 0, "groups_failed": 0,
                  "source": "fallback", "latency_ms": 0, "errors": [],
                  "params": {"temperature": temperature, "max_tokens": max_tokens,
                             "retries": retries, "group_size": group_size}}
    if not use_llm or client is None:
        out = {k: slots.get(k, _NARRATIVE_FALLBACK[k]) for k in _LLM_SLOTS}
        out["_meta"] = dict(meta)  # 保留 params，使降级路径的元数据结构一致
        return out

    context = {k: slots[k] for k in (
        "task_id", "roster_total", "roster_by_category", "readiness_level",
        "n_runs", "pred_k", "pred_m", "pred_f", "pred_attrition", "pred_damage_rate",
        "pred_readiness", "carry_stock", "operating_stock", "depot_stock",
        "repair_teams", "arv_count", "supply_veh") if k in slots}

    for name, keys in groups:
        chat = None
        for attempt in range(retries + 1):
            chat = client.chat(_llm_system(), _llm_user(name, keys, context),
                               temperature=temperature, max_tokens=max_tokens)
            if chat.ok and _extract_json(chat.text):
                break
        meta["attempted"] = True
        if not chat or not chat.ok:
            meta["groups_failed"] += 1
            meta["errors"].append(f"{name}: {(chat.error if chat else '无响应')}")
            continue
        obj = _extract_json(chat.text)
        if not obj:
            meta["groups_failed"] += 1
            meta["errors"].append(f"{name}: 响应不是合法 JSON（重试 {retries} 次）")
            continue
        got = 0
        for key in keys:
            val = str(obj.get(key, "")).strip()
            # 数字一致性红线：叙述里出现数字即判无效，用默认文字替换
            if not val or _NUM_RE.search(val) or len(val) > 120:
                continue
            result[key] = val
            got += 1
        if got == len(keys):
            meta["groups_ok"] += 1
            meta["source"] = "local_llm"
        else:
            meta["groups_failed"] += 1
            meta["errors"].append(f"{name}: {len(keys) - got} 个槽位无效（含数字或缺失）")
        meta["latency_ms"] += chat.latency_ms

    for key in _LLM_SLOTS:
        result.setdefault(key, slots.get(key, _NARRATIVE_FALLBACK[key]))
    result["_meta"] = meta
    return result


_SOURCE_LABEL = {
    "mission": "录入数据", "loss": "战损清单", "rates": "指标派生",
    "generated": "系统生成", "decision": "决策阈值", "scenario": "想定环境",
    "template": "模板占位", "llm": "本地大模型叙述",
}


def build_provenance(slots: dict, text: str) -> dict:
    """数字溯源（P0-8）：方案正文里每个数值槽位 → 来源/定义/值。

    返回一个按槽位 ID 索引的字典；前端点击正文数字时按"槽位值"反查并高亮来源。
    只覆盖确定性数值槽位（LLM 叙述槽位不产生权威数字）。
    """
    prov: dict[str, dict] = {}
    for key, meta in SLOT_DICT.items():
        src = meta.get("source", "")
        if src == "llm":
            continue
        val = slots.get(key)
        if val is None or val == "":
            continue
        sval = str(val)
        prov[key] = {
            "label": meta.get("label", key),
            "source": src,
            "source_label": _SOURCE_LABEL.get(src, src),
            "section": meta.get("section", ""),
            "value": sval,
            "in_text": sval in text,
        }
    return {"slots": prov, "source_labels": _SOURCE_LABEL,
            "note": "数值槽位来源映射；点击方案正文数字可查对应来源与定义"}


def validate_document(text: str, slots: dict, *, strict: bool = False) -> dict:
    """数字一致性复核：未替换槽位 + 叙述中的数字 + 关键数字出现性。"""
    missing = sorted(set(_SLOT_RE.findall(text)))
    numeric_keys = [k for k, v in SLOT_DICT.items() if v.get("source") in
                    ("mission", "loss", "rates", "generated", "decision", "scenario")]
    # 叙述槽位中不得出现数字（LLM 编造检测）
    fabricated = []
    for key in _LLM_SLOTS:
        val = str(slots.get(key, ""))
        if _NUM_RE.search(val):
            fabricated.append({"slot": key, "value": val[:60]})
    # 关键数字是否真的出现在文档中
    absent = []
    for key in ("pred_k", "pred_m", "pred_f", "pred_attrition", "roster_total",
                "need_mobility", "pred_readiness"):
        if str(slots.get(key, "")) not in text:
            absent.append(key)
    passed = not missing and not fabricated and not absent
    return {"passed": passed, "strict": strict, "unfilled_slots": missing,
            "fabricated_numbers": fabricated, "absent_key_numbers": absent,
            "checked_numeric_slots": len(numeric_keys)}


def fill_template(text: str, slots: dict) -> str:
    def repl(m: re.Match) -> str:
        key = m.group(1)
        return str(slots.get(key, m.group(0)))
    return _SLOT_RE.sub(repl, text)


# ---------------- 主入口 ----------------

def generate(
    mission: dict,
    loss: dict,
    *,
    rates: dict | None = None,
    metrics: dict | None = None,
    scenario_info: dict | None = None,
    template: str = DEFAULT_TEMPLATE,
    use_llm: bool = True,
    client=None,
    llm_params: dict | None = None,
) -> dict:
    """生成完整保障方案（不直接导出 docx，导出复用 build_report_docx.py）。"""
    slots = build_slots(mission, loss, rates=rates, metrics=metrics,
                        scenario_info=scenario_info, plan_id=f"PQ-{uuid.uuid4().hex[:8].upper()}")
    if use_llm and client is None:
        try:
            from ..agent.llm_client import LocalLLMClient
            client = LocalLLMClient()
        except Exception:  # noqa: BLE001
            client = None
    narratives = generate_narratives(slots, client=client, use_llm=use_llm and client is not None,
                                     llm_params=llm_params)
    llm_meta = narratives.pop("_meta", {})
    if client is not None:
        llm_meta["model"] = getattr(getattr(client, "config", None), "model", "")
    slots.update(narratives)
    text = fill_template(read_template(template), slots)
    validation = validate_document(text, slots)
    provenance = build_provenance(slots, text)
    return {
        "plan_id": slots["plan_id"],
        "mission_id": mission.get("mission_id", ""),
        "loss_id": loss.get("loss_id", ""),
        "template": template,
        "text": text,
        "slots": slots,
        "llm": llm_meta,
        "validation": validation,
        "provenance": provenance,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def apply_overrides(plan: dict, overrides: dict, *, lock: bool = True) -> dict:
    """P1-2：人工覆写 LLM 叙述槽位，重新填模板并复核（数字槽位不可改）。

    - 只接受 `SLOT_DICT[slot].source == "llm"` 的槽位；
    - 覆写值同样禁止阿拉伯数字（防止人工引入与清单冲突的数字）；
    - `lock=True` 时在方案上标记 `narrative_locked`，供后续重新生成时提示。
    """
    slots = dict(plan.get("slots") or {})
    llm_slots = {k for k, v in SLOT_DICT.items() if v.get("source") == "llm"}
    rejected: list[dict] = []
    applied: list[str] = []
    for key, val in (overrides or {}).items():
        key = str(key)
        if key not in llm_slots:
            rejected.append({"slot": key, "reason": "非叙述槽位，数字由清单派生不可覆写"})
            continue
        text = str(val).strip()
        if not text:
            rejected.append({"slot": key, "reason": "空值"})
            continue
        if _NUM_RE.search(text):
            rejected.append({"slot": key, "reason": "包含数字，叙述槽位禁止数字"})
            continue
        slots[key] = text
        applied.append(key)
    template = plan.get("template") or DEFAULT_TEMPLATE
    text = fill_template(read_template(template), slots)
    validation = validate_document(text, slots)
    out = dict(plan)
    out.update({
        "text": text, "slots": slots, "validation": validation,
        "provenance": build_provenance(slots, text),
        "narrative_locked": bool(lock),
        "narrative_overridden": applied,
        "override_rejected": rejected,
        "override_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    return out


def to_docx_bytes(plan: dict) -> bytes:
    """保障方案 → 公文式 docx 字节流（P0-7）。

    自包含实现，复用报告 docx 的版式规范（A4/仿宋四号/黑体标题/固定行距），
    不 import `report/build_report_docx.py`（该模块在 import 时读 sys.argv，有副作用）。
    """
    from docx import Document
    from docx.shared import Cm, Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn

    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.top_margin = sec.bottom_margin = Cm(2.54)
    sec.left_margin = sec.right_margin = Cm(2.54)
    normal = doc.styles["Normal"]
    normal.font.name = "仿宋"
    normal.font.size = Pt(14)
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "仿宋")

    def add_para(text: str, *, bold=False, size=14, font="仿宋", center=False, indent=True):
        p = doc.add_paragraph()
        p.paragraph_format.line_spacing = Pt(26)
        if indent:
            p.paragraph_format.first_line_indent = Pt(size * 2)
        if center:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(text)
        run.bold = bold
        run.font.size = Pt(size)
        run.font.name = font
        run._element.rPr.rFonts.set(qn("w:eastAsia"), font)
        return p

    for raw in str(plan.get("text", "")).splitlines():
        line = raw.strip()
        if not line:
            continue
        # Markdown 加粗标题行 **...** → 黑体标题；表格/分隔线按正文保留
        if line.startswith("**") and line.endswith("**") and "｜" not in line and "|" not in line:
            add_para(line.strip("*"), bold=True, size=16, font="黑体", center=True, indent=False)
        elif line.startswith("##") or line.startswith("#"):
            add_para(line.lstrip("# ").strip(), bold=True, size=16, font="黑体", indent=False)
        elif set(line) <= set("-—_"):
            continue
        elif line.startswith("|"):
            add_para(line, indent=False)
        else:
            add_para(line)

    import io
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def save(plan: dict) -> str:
    pid = plan.get("plan_id") or uuid.uuid4().hex[:10]
    plan["plan_id"] = pid
    _STORE.mkdir(parents=True, exist_ok=True)
    (_STORE / f"plan_{pid}.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (_STORE / f"plan_{pid}.md").write_text(plan.get("text", ""), encoding="utf-8")
    return pid


def load(pid: str) -> dict | None:
    f = _STORE / f"plan_{pid}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None


def latest() -> dict | None:
    files = sorted(_STORE.glob("plan_*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
    return json.loads(files[0].read_text(encoding="utf-8")) if files else None
