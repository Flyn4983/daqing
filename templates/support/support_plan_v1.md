**编号：{{plan_id}}　　{{plan_name}}**

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
