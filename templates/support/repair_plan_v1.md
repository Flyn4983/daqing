**编号：{{plan_id}}　　{{plan_name}}**

**一、报损情况**
（一）接报来源与时机：{{enemy_situation}}。战损装备由一线观察哨与分队逐级上报，报损要素含单位、装备、等级、部位与位置。
（二）损伤判别：{{threat_type}}；敌主要毁伤手段为 {{enemy_fire}}。
（三）上级抢修要求：{{superior_intent}}。参战装备 {{roster_total}} 台（套），战备等级 {{readiness_level}}。

**二、抢修任务与决心**
（一）抢修目标：装备完好率保持在 {{target_readiness}} 以上，战损装备抢救抢修及时率不低于 {{target_repair_timeliness}}，任务完成率不低于 {{target_success}}。
（二）抢修原则：先抢后修、先重后轻、就便就位、分级处置、修复归队。
（三）抢修决心：以 {{repair_teams}} 个抢修组、{{arv_count}} 台抢救车、{{supply_veh}} 台器材车编成抢修群，配置于 {{support_area}}，按 K/F/M 三级分流处置。

**三、战损统计与抢修量测算**
（一）战损统计：本次 {{n_runs}} 次统计，战毁 {{pred_k}} 台、机动受损 {{pred_m}} 台、火力受损 {{pred_f}} 台；战损率 {{pred_attrition}}，战斗损伤率 {{pred_damage_rate}}，完好率 {{pred_readiness}}。
（二）抢修量：待抢修 {{pred_m}} 台（机动受损）、{{pred_f}} 台（火力受损），预计修复 {{est_repaired}} 台回归战斗序列。
（三）器材需求：行走总成 {{need_mobility}} 件、动力总成 {{need_powerpack}} 件、火控系统 {{need_fire_control}} 件、武器系统 {{need_weapon}} 件；携行/运行/基地储备 {{carry_stock}} / {{operating_stock}} / {{depot_stock}}。

**四、分级抢修处置**
（一）M 级（机动受损）：{{m_repair_rule}}，抢修优先顺序先 {{repair_priority_1}}，后 {{repair_priority_2}}。
（二）F 级（火力受损）：{{f_repair_site}}，需后送的按第七节组织。
（三）K 级（战毁）：{{k_disposal}}，不再列入抢修量。

**五、抢修力量编组与任务区分**
（一）抢修指挥组：{{cp_location}}，由 {{cp_composition}} 组成。
（二）抢修组：{{repair_teams}} 组，任务区分 {{repair_task_split}}。
（三）抢救（拖救）组：{{arv_count}} 台，配置 {{arv_location}}。
（四）器材保障组：{{supply_veh}} 台，配置 {{supply_location}}。

**六、器材前送与补给**
（一）器材前送：每 {{wave_interval_min}} 分钟一波、每波 {{wave_qty}} 件、前送半径 {{dist_radius_m}} 米。
（二）弹药保障：{{ammo_delivery_plan}}。
（三）油料保障：{{fuel_delivery_plan}}。

**七、拖救与后送**
（一）拖救/后送路线：{{evac_routes}}。
（二）后送工具：{{evac_assets}}。
（三）后送顺序：{{evac_priority}}。
（四）后送时限：{{evac_timeline}}。

**八、防卫、通信与运输**
（一）抢修群防卫：{{defense_plan}}。
（二）通信保障：{{comm_plan}}。
（三）运输保障：{{transport_plan}}。

**九、实施步骤与报告**
（一）{{phase_1}}；（二）{{phase_2}}；（三）{{phase_3}}。
报告时机：{{report_schedule}}；情况变化时按 {{revision_rule}} 修订。

**十、附表**
{{table_loss}}
{{table_spares}}
{{table_org}}
{{map_deploy}}
{{table_schedule}}

---
*本方案数字来自战损清单（仿真计算或人工填报）与录入数据，叙述由本地大模型依模板生成并经数字一致性校验；战损等级口径：K 战毁、F 火力受损、M 机动受损。*
