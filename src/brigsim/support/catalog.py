"""标准资源目录 + 作战保障全过程节点定义。

目录来自 example_brigade.yaml 的 23 类平台 + 3 类空中架次、AmmoClassName、
四率口径与三级器材；油料/日常业务为**方案层新增项**（不进仿真内核，
因此不会改变四率权威数字——见《与合成旅保障对齐_需求与改造分析_v1.md》§2.6）。
"""

from __future__ import annotations

# ---- 装备目录：键与 platform_types / AirPackage.kind 对齐 ----
EQUIPMENT: list[dict] = [
    {"key": "tank", "name": "主战坦克", "category": "assault", "group": "突击力量"},
    {"key": "ifv", "name": "步兵战车", "category": "assault", "group": "突击力量"},
    {"key": "atgm_team", "name": "反坦克导弹组", "category": "assault", "group": "突击力量"},
    {"key": "mortar", "name": "120 自行迫击炮", "category": "artillery", "group": "火力打击"},
    {"key": "howitzer", "name": "122 自行榴弹炮", "category": "artillery", "group": "火力打击"},
    {"key": "rocket", "name": "122 火箭炮", "category": "artillery", "group": "火力打击"},
    {"key": "hj10", "name": "红箭-10 导弹车", "category": "missile", "group": "火力打击"},
    {"key": "hq17", "name": "红旗-17 防空导弹", "category": "air_defense", "group": "防空"},
    {"key": "pgz09", "name": "双35 自行高炮", "category": "air_defense", "group": "防空"},
    {"key": "radar_air", "name": "对空警戒雷达", "category": "radar_air", "group": "侦察电抗"},
    {"key": "radar_cb", "name": "反炮兵定位雷达", "category": "radar_cb", "group": "侦察电抗"},
    {"key": "recon_veh", "name": "装甲侦察车", "category": "recon", "group": "侦察电抗"},
    {"key": "ew_radar", "name": "雷达对抗车", "category": "ew", "group": "侦察电抗"},
    {"key": "ew_comms", "name": "通信对抗车", "category": "ew", "group": "侦察电抗"},
    {"key": "ew_gen", "name": "通用电抗车", "category": "ew", "group": "侦察电抗"},
    {"key": "c2_veh", "name": "指挥通信车", "category": "c2", "group": "指挥"},
    {"key": "eng_veh", "name": "扫雷破障车", "category": "engineer", "group": "工程防化"},
    {"key": "smoke_veh", "name": "发烟车", "category": "engineer", "group": "工程防化"},
    {"key": "arv", "name": "装甲抢救车", "category": "support", "group": "保障车辆"},
    {"key": "repair_veh", "name": "装甲抢修车", "category": "support", "group": "保障车辆"},
    {"key": "supply_veh", "name": "器材补给车", "category": "support", "group": "保障车辆"},
    {"key": "ammo_veh", "name": "弹药补给车", "category": "support", "group": "保障车辆"},
    {"key": "dummy_arty", "name": "假目标/假阵地", "category": "support", "group": "伪装"},
    {"key": "uav", "name": "侦察校射无人机", "category": "recon", "group": "空中架次"},
    {"key": "loitering", "name": "巡飞弹", "category": "missile", "group": "空中架次"},
    {"key": "helo", "name": "武装直升机", "category": "assault", "group": "空中架次"},
]

# ---- 弹药目录：class 与 AmmoClassName 对齐 ----
AMMO: list[dict] = [
    {"class": "ap", "name": "穿甲弹", "unit": "发", "for": "坦克/反坦克"},
    {"class": "autocannon", "name": "机关炮弹", "unit": "发", "for": "步战/高炮"},
    {"class": "atgm", "name": "反坦克导弹", "unit": "枚", "for": "反坦克组/红箭-10"},
    {"class": "indirect", "name": "间瞄榴弹/火箭弹", "unit": "发", "for": "迫击炮/榴弹炮/火箭炮"},
    {"class": "air_missile", "name": "防空导弹", "unit": "枚", "for": "红旗-17"},
    {"class": "mine", "name": "地雷/障碍器材", "unit": "枚", "for": "工兵"},
]

# ---- 油料目录：方案层新增，不进内核 ----
FUEL: list[dict] = [
    {"grade": "柴油", "unit": "升", "note": "主战装备/保障车辆"},
    {"grade": "航空煤油", "unit": "升", "note": "直升机/航空器"},
    {"grade": "汽油", "unit": "升", "note": "小型发电机/勤务车辆"},
    {"grade": "润滑油脂", "unit": "千克", "note": "保养/维护"},
]

# ---- 保障车辆目录（与 ENTITY_GROUPS / FEATURES 对齐）----
SUPPORT_VEHICLES: list[dict] = [
    {"key": "repair_veh", "name": "装甲抢修车", "knob": "red_repair_teams", "unit": "台"},
    {"key": "arv", "name": "装甲抢救车", "knob": "red_arv", "unit": "台"},
    {"key": "supply_veh", "name": "器材补给车", "knob": "red_supply_veh", "unit": "台"},
    {"key": "ammo_veh", "name": "弹药补给车", "knob": None, "unit": "台"},
    {"key": "tanker", "name": "油料补给车（方案层）", "knob": None, "unit": "台"},
]

# ---- 其他保障资源：三级器材 + 通用消耗品 ----
RESOURCES: list[dict] = [
    {"key": "mobility", "name": "行走总成", "echelon": ["carry", "operating", "depot"], "unit": "件"},
    {"key": "powerpack", "name": "动力总成", "echelon": ["carry", "operating", "depot"], "unit": "件"},
    {"key": "fire_control", "name": "火控系统", "echelon": ["carry", "operating", "depot"], "unit": "件"},
    {"key": "weapon", "name": "武器系统", "echelon": ["carry", "operating", "depot"], "unit": "件"},
    {"key": "rations", "name": "给养", "echelon": ["carry", "operating", "depot"], "unit": "份"},
    {"key": "medical", "name": "卫勤器材", "echelon": ["carry", "operating", "depot"], "unit": "套"},
    {"key": "engineer", "name": "工程器材", "echelon": ["carry", "operating", "depot"], "unit": "套"},
]

# ---- 日常业务要素（方案层上下文，不进内核）----
DAILY_OPS: list[dict] = [
    {"key": "readiness_level", "name": "战备等级", "options": ["一级战备", "二级战备", "三级战备", "日常战备"]},
    {"key": "duty_roster", "name": "装备值勤", "options": ["值班", "值日", "昼夜值守"]},
    {"key": "training_plan", "name": "训练计划", "options": ["周维护 4h", "日检 1h", "专项强化"]},
    {"key": "supply_request_approval", "name": "请领审批", "options": ["连级审批", "营级审批", "旅级审批"]},
    {"key": "maintenance_plan", "name": "维护保养", "options": ["日检/周检", "一级保养", "二级保养"]},
]

CATALOG: dict = {
    "equipment": EQUIPMENT,
    "ammo": AMMO,
    "fuel": FUEL,
    "support_vehicles": SUPPORT_VEHICLES,
    "resources": RESOURCES,
    "daily_ops": DAILY_OPS,
    "grade_names": {"0": "无损", "1": "M 机动受损", "2": "F 火力受损", "3": "K 战毁"},
    "disposition_names": {
        "0": "未处置", "1": "现地处置/抢修", "2": "拖救", "3": "自行后送",
        "4": "后送", "5": "报废",
    },
    "part_names": {
        "mobility": "行走总成", "powerpack": "动力总成",
        "fire_control": "火控系统", "weapon": "武器系统",
        "hull": "车体（致命）", "ammo_rack": "弹药架（致命）",
    },
    "mission_types": ["进攻", "防御", "机动", "保障演练"],
    "scenarios": ["medium_brigade", "brigade_canyon_clear", "brigade_plain_fog",
                  "brigade_rolling_fortified", "example_brigade", "example_battalion",
                  "real_terrain_brigade"],
}

# ---- 作战保障全过程节点：前端流程条的单一数据源 ----
# status 由前端根据后端数据计算：idle / ready / running / done / error
PROCESS_NODES: list[dict] = [
    {"id": "input", "idx": 1, "name": "任务与资源录入", "en": "INPUT",
     "desc": "装备/弹药/油料/车辆/器材/日常业务要素及数量", "api": "/api/support/mission"},
    {"id": "validate", "idx": 2, "name": "编成校验与配置生成", "en": "VALIDATE",
     "desc": "数量/枚举校验，映射为 7 保障旋钮 + 任务参数", "api": "/api/support/mission"},
    {"id": "deduce", "idx": 3, "name": "多次对抗推演", "en": "DEDUCE",
     "desc": "MC N 次复制，逐单位 M/F/K 与处置方式落账", "api": "/api/support/loss"},
    {"id": "loss", "idx": 4, "name": "战损清单生成", "en": "LOSS LIST",
     "desc": "逐件清单 + 按类别/等级/处置多维汇总", "api": "/api/support/loss"},
    {"id": "rates", "idx": 5, "name": "战损率等指标派生", "en": "METRICS",
     "desc": "清单→战损率/完好率/修复率/故障率，与内核对账", "api": "/api/support/loss"},
    {"id": "plan", "idx": 6, "name": "保障方案生成", "en": "PLAN",
     "desc": "槽位确定性填充 + 模板；LLM 仅写叙述文字", "api": "/api/support/plan"},
    {"id": "review", "idx": 7, "name": "数字一致性复核", "en": "REVIEW",
     "desc": "扫描方案数字 ↔ 清单数据，不一致阻断或回退", "api": "/api/support/plan"},
    {"id": "export", "idx": 8, "name": "清单/方案导出", "en": "EXPORT",
     "desc": "CSV 战损清单 + Markdown/Word 保障方案", "api": "/api/support/export"},
]

# ---- 方案模板槽位字典：slot_id → 数据来源 ----
SLOT_DICT: dict[str, dict] = {
    "plan_id": {"source": "generated", "label": "方案编号"},
    "plan_name": {"source": "mission", "label": "方案名称"},
    "task_id": {"source": "mission", "label": "任务编号"},
    "enemy_situation": {"source": "llm", "label": "敌情", "section": "一、情况判断"},
    "enemy_fire": {"source": "llm", "label": "敌主要火力", "section": "一、情况判断"},
    "threat_type": {"source": "llm", "label": "威胁类型", "section": "一、情况判断"},
    "enemy_strength_desc": {"source": "mission", "label": "敌方兵力编成（GUI录入）"},
    "force_balance_desc": {"source": "mission", "label": "红蓝兵力对比（GUI录入）"},
    "roster_total": {"source": "mission", "label": "参战装备总数"},
    "roster_by_category": {"source": "mission", "label": "装备编成构成"},
    "readiness_level": {"source": "mission", "label": "战备等级"},
    "terrain_desc": {"source": "scenario", "label": "地形"},
    "weather_desc": {"source": "scenario", "label": "天候"},
    "visibility_desc": {"source": "scenario", "label": "能见度"},
    "superior_intent": {"source": "llm", "label": "上级意图", "section": "一、情况判断"},
    "target_readiness": {"source": "decision", "label": "完好率目标"},
    "target_success": {"source": "decision", "label": "完成率目标"},
    "target_repair_timeliness": {"source": "decision", "label": "抢修及时率目标"},
    "repair_teams": {"source": "mission", "label": "抢修组数"},
    "arv_count": {"source": "mission", "label": "抢救车数"},
    "supply_veh": {"source": "mission", "label": "器材车数"},
    "support_area": {"source": "llm", "label": "保障群配置地域", "section": "二、保障任务与决心"},
    "n_runs": {"source": "loss", "label": "推演次数"},
    "pred_k": {"source": "loss", "label": "预计战毁数"},
    "pred_m": {"source": "loss", "label": "预计机动受损数"},
    "pred_f": {"source": "loss", "label": "预计火力受损数"},
    "pred_attrition": {"source": "rates", "label": "预计战损率"},
    "pred_damage_rate": {"source": "rates", "label": "战斗损伤率（K+M+F）"},
    "pred_success": {"source": "loss", "label": "任务完成率"},
    "pred_readiness": {"source": "rates", "label": "完好率"},
    "need_mobility": {"source": "loss", "label": "行走总成需求"},
    "need_powerpack": {"source": "loss", "label": "动力总成需求"},
    "need_fire_control": {"source": "loss", "label": "火控系统需求"},
    "need_weapon": {"source": "loss", "label": "武器系统需求"},
    "carry_stock": {"source": "mission", "label": "携行储备"},
    "operating_stock": {"source": "mission", "label": "运行储备"},
    "depot_stock": {"source": "mission", "label": "基地储备"},
    "ammo_by_class": {"source": "mission", "label": "弹药需求"},
    "ammo_wave_qty": {"source": "mission", "label": "弹药前送量"},
    "fuel_consumption_l": {"source": "mission", "label": "油料消耗预计"},
    "fuel_stock_l": {"source": "mission", "label": "油料储备"},
    "tanker_count": {"source": "mission", "label": "油罐车数"},
    "fuel_resupply_l": {"source": "mission", "label": "油料前送量"},
    "repair_task_split": {"source": "llm", "label": "抢修任务区分", "section": "四、保障力量编组与部署"},
    "cp_location": {"source": "llm", "label": "保障指挥所位置", "section": "四、保障力量编组与部署"},
    "cp_composition": {"source": "llm", "label": "指挥所组成", "section": "四、保障力量编组与部署"},
    "arv_location": {"source": "llm", "label": "抢救组配置", "section": "四、保障力量编组与部署"},
    "supply_location": {"source": "llm", "label": "器材组配置", "section": "四、保障力量编组与部署"},
    "ammo_fuel_location": {"source": "llm", "label": "弹药/油料组配置", "section": "四、保障力量编组与部署"},
    "m_repair_rule": {"source": "llm", "label": "M 级抢修规则", "section": "五、抢救抢修计划"},
    "f_repair_site": {"source": "llm", "label": "F 级修理点", "section": "五、抢救抢修计划"},
    "k_disposal": {"source": "llm", "label": "K 级处置", "section": "五、抢救抢修计划"},
    "repair_priority_1": {"source": "llm", "label": "抢修优先顺序1", "section": "五、抢救抢修计划"},
    "repair_priority_2": {"source": "llm", "label": "抢修优先顺序2", "section": "五、抢救抢修计划"},
    "est_repaired": {"source": "loss", "label": "预计修复台数"},
    "wave_interval_min": {"source": "mission", "label": "前送波次节拍"},
    "wave_qty": {"source": "mission", "label": "每波件数"},
    "dist_radius_m": {"source": "mission", "label": "前送半径"},
    "ammo_delivery_plan": {"source": "llm", "label": "弹药前送方案", "section": "六、补给计划"},
    "fuel_delivery_plan": {"source": "llm", "label": "油料补给方案", "section": "六、补给计划"},
    "evac_routes": {"source": "llm", "label": "后送路线", "section": "七、装备后送计划"},
    "evac_assets": {"source": "llm", "label": "后送工具", "section": "七、装备后送计划"},
    "evac_priority": {"source": "llm", "label": "后送顺序", "section": "七、装备后送计划"},
    "evac_timeline": {"source": "llm", "label": "后送时限", "section": "七、装备后送计划"},
    "defense_plan": {"source": "llm", "label": "防卫", "section": "八、防卫通信运输"},
    "comm_plan": {"source": "llm", "label": "通信", "section": "八、防卫通信运输"},
    "transport_plan": {"source": "llm", "label": "运输", "section": "八、防卫通信运输"},
    "phase_1": {"source": "llm", "label": "实施步骤1", "section": "九、实施步骤"},
    "phase_2": {"source": "llm", "label": "实施步骤2", "section": "九、实施步骤"},
    "phase_3": {"source": "llm", "label": "实施步骤3", "section": "九、实施步骤"},
    "report_schedule": {"source": "llm", "label": "报告时机", "section": "九、实施步骤"},
    "revision_rule": {"source": "llm", "label": "修订规则", "section": "九、实施步骤"},
    "table_loss": {"source": "loss", "label": "附表1 战损表"},
    "table_spares": {"source": "loss", "label": "附表2 器材需求表"},
    "table_org": {"source": "mission", "label": "附表3 力量编成表"},
    "map_deploy": {"source": "template", "label": "附表4 部署图"},
    "table_schedule": {"source": "llm", "label": "附表5 时序表", "section": "十、附表"},
    # ---- P3：真实业务约束（三源）——全部确定性，来自 GUI 录入/校核，LLM 不得改写 ----
    "constraint_summary": {"source": "mission", "label": "资源与能力约束（GUI录入）"},
    "constraint_deadline": {"source": "mission", "label": "上级时限与可修等级（GUI录入）"},
    "constraint_order": {"source": "mission", "label": "上级命令原文（GUI录入）"},
    "constraint_priority": {"source": "mission", "label": "抢修优先规则（GUI录入）"},
    "constraint_feasibility": {"source": "decision", "label": "约束可行性校核结论"},
}

# ---- P1-7：指标口径字典（tooltip 单一数据源，纯展示，不改内核计算）----
METRIC_GLOSSARY: dict[str, dict] = {
    "attrition": {
        "name": "战损率", "unit": "%",
        "formula": "K 级战毁数 / 我方参战总数 = 1 - alive_n / n",
        "numerator": "damage_grade == K（不可恢复战毁）",
        "denominator": "我方参战单位数（红方，含新增保障车实体）",
        "authoritative": True,
        "note": "与内核 four_rates_summary().attrition_red 等价，可对账；M/F 可修不计入。",
    },
    "damage_rate": {
        "name": "战斗损伤率", "unit": "%",
        "formula": "(K + M + F) / 我方参战总数",
        "numerator": "K 战毁 + M 机动受损 + F 火力受损",
        "denominator": "我方参战单位数",
        "authoritative": False,
        "note": "口径 B，含可修损伤，反映广义受损；不替代战损率、不与内核对账。",
    },
    "readiness": {
        "name": "完好率", "unit": "%",
        "formula": "(完好且无损伤且不在修) / 我方参战总数",
        "numerator": "alive 且 not damaged 且 not under_repair",
        "denominator": "我方参战单位数",
        "authoritative": True, "note": "清单派生值与内核 readiness_red 对账（容差 1e-3）。",
    },
    "repair_rate": {
        "name": "修复率", "unit": "%",
        "formula": "已修复完成数 / (已修复 + 在修)",
        "numerator": "disposition 为现地抢修/修复完成的条目",
        "denominator": "已修复完成 + 仍在修",
        "authoritative": False, "note": "仅统计有维修需求的单位，未受损单位不进分母。",
    },
    "failure_rate": {
        "name": "故障率", "unit": "次/百车·小时",
        "formula": "自然故障次数 / 暴露量 × 3600 / dt × 100",
        "numerator": "failures_natural（非战斗损伤）",
        "denominator": "装备暴露量（数量 × 时长）",
        "authoritative": True, "note": "口径见 docs/data-interface.md L144-155。",
    },
    "success": {
        "name": "任务完成率", "unit": "%",
        "formula": "达成任务条件的复制数 / 总复制数",
        "numerator": "N 次 MC 中 success == True 的次数",
        "denominator": "MC 复制次数 N",
        "authoritative": False, "note": "来自 MC 汇总，非清单逐件派生。",
    },
    "ci95": {
        "name": "95% 置信区间", "unit": "同指标",
        "formula": "均值 ± 1.96 × sd / √N",
        "numerator": "样本标准差 sd（正态近似）",
        "denominator": "复制次数 N 的平方根",
        "authoritative": False, "note": "与 experiment/metrics.summarize 口径一致。",
    },
}

# ---- P1-3：LLM 生成参数规格（前端高级参数面板的单一数据源）----
LLM_PARAM_SPEC: list[dict] = [
    {"key": "temperature", "name": "生成温度", "default": 0.25, "min": 0.0, "max": 1.0,
     "step": 0.05, "hint": "越低越稳定；公文叙述建议 0.2~0.4"},
    {"key": "max_tokens", "name": "单段最大 token", "default": 900, "min": 256, "max": 4096,
     "step": 128, "hint": "27B CPU 上过大显著变慢；默认 900"},
    {"key": "retries", "name": "单段重试次数", "default": 1, "min": 0, "max": 3,
     "step": 1, "hint": "响应不是合法 JSON 时重试；0=不重试直接降级"},
    {"key": "group_size", "name": "分段数", "default": 4, "min": 1, "max": 4,
     "step": 1, "hint": "叙述分几段生成；段数越多单次内存压力越小"},
]

# ---- P2-4：数据字典 —— 战损清单字段（集中查询，纯展示不改内核）----
LOSS_FIELD_DICT: list[dict] = [
    {"key": "loss_id", "name": "战损清单编号", "type": "str", "source": "generated", "note": "单次推演清单唯一标识"},
    {"key": "tick", "name": "发生时刻", "type": "int", "source": "事件流", "note": "仿真时钟 tick"},
    {"key": "side", "name": "所属方别", "type": "int/str", "source": "事件流", "note": "0=红方(我方), 1=蓝方(敌方)；派生四率只统计红方"},
    {"key": "unit_id", "name": "单位编号", "type": "str", "source": "事件流", "note": "3D 定位实体路径 world/units/{unit_id}"},
    {"key": "platform", "name": "装备平台", "type": "str", "source": "想定/事件流", "note": "平台类型标识，不在目录时回退原始 type"},
    {"key": "category", "name": "装备类别", "type": "str", "source": "目录", "note": "assault/support/... 用于多维汇总"},
    {"key": "org", "name": "编制单位", "type": "str", "source": "录入", "note": "所属营连/保障群"},
    {"key": "grade", "name": "损伤等级", "type": "str", "source": "事件流", "note": "M 机动受损 / F 火力受损 / K 战毁；战损率只认 K"},
    {"key": "damage_part", "name": "受损部件", "type": "str", "source": "事件流", "note": "mobility/powerpack/fire_control/weapon/致命部位"},
    {"key": "cause", "name": "损伤原因", "type": "str", "source": "事件流", "note": "战斗损伤 / 自然故障"},
    {"key": "weapon", "name": "致伤武器", "type": "str", "source": "事件流", "note": "命中该单位的武器类型"},
    {"key": "disposition", "name": "处置方式", "type": "str", "source": "事件流", "note": "现地抢修/拖救/自行后送/后送/报废"},
    {"key": "repair_status", "name": "修复状态", "type": "str", "source": "事件流", "note": "待修/在修/已修复/报废"},
    {"key": "est_repair_ticks", "name": "预计修复工时", "type": "int", "source": "事件流", "note": "按部件与保障能力估算"},
    {"key": "location", "name": "地理位置", "type": "str", "source": "事件流", "note": "网格坐标，供 3D 定位卡展示"},
    {"key": "source", "name": "数据来源", "type": "str", "source": "generated", "note": "event=事件流实测 / estimate=MC 汇总估算"},
]

# ---- P2-5：成本权重规格（录入侧成本约束；不进内核，仅方案层与对比展示）----
COST_WEIGHT_SPEC: list[dict] = [
    {"key": "attrition_weight", "name": "战损权重", "default": 0.4, "min": 0.0, "max": 1.0, "step": 0.05,
     "hint": "方案评估中战损率的相对重要性"},
    {"key": "time_weight", "name": "时效权重", "default": 0.3, "min": 0.0, "max": 1.0, "step": 0.05,
     "hint": "抢修/补给时效的相对重要性"},
    {"key": "cost_weight", "name": "成本权重", "default": 0.3, "min": 0.0, "max": 1.0, "step": 0.05,
     "hint": "保障资源消耗的相对重要性"},
    {"key": "budget_limit", "name": "成本上限(万元)", "default": 0, "min": 0, "max": 1000000, "step": 10,
     "hint": "0=不设上限；用于方案成本约束校核展示"},
]

# ---- P2-3：方案签批工作流状态机 ----
REVIEW_STATES: list[dict] = [
    {"id": "draft", "name": "草案", "next": ["reviewing"]},
    {"id": "reviewing", "name": "复核中", "next": ["approved", "draft"]},
    {"id": "approved", "name": "已定稿", "next": ["reviewing"]},
]
REVIEW_ACTIONS: dict[str, dict] = {
    "submit": {"from": ["draft"], "to": "reviewing", "name": "提交复核"},
    "approve": {"from": ["reviewing"], "to": "approved", "name": "复核定稿"},
    "reject": {"from": ["reviewing"], "to": "draft", "name": "退回修改"},
    "reopen": {"from": ["approved"], "to": "reviewing", "name": "重新复核"},
}
