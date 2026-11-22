# 真实业务约束 GUI 录入与校核（P3）实现说明 v1

> 日期：2026-11-22　｜　用户确认路线：**方案层+选优生效、硬阻断+软告警**
> 目标：把报告"三源约束"从展示性映射，变成 GUI 可设定、可校核、可参与选优的真实输入。

---

## 1. 问题与依据

### 1.1 用户诉求
报告中的约束域/可行域，应是**实际业务约束**：修理车上限、人员上限、人员修理能力（包括但不限于），需要能在 GUI 设定；抢修时限、上级命令等规则性限制也需前期 GUI 输入。

### 1.2 核查结论（改前事实）
- 报告《创新点重构_模型公式数据.md》§创新点③定义三源约束：战损物理源、指挥权威源、资源现实源，硬约束 vs 软约束。
- 软件此前仅做**展示性映射**：`research.js` 自述"三源分类是报告的设计方法；软件将资源/编制来源落到成本权重与档位边界"；7 旋钮上下界写死在 `surrogate/design.py:56-68`，用户改不了。
- 保障录入只有车辆数量、目标阈值、成本权重，**无上限/能力/时限/上级命令**字段；"后送时限"槽位甚至由 LLM 生成（`catalog.py:196` `source=llm`）。

---

## 2. 红线与决策

| 项 | 决策 |
|---|---|
| 约束生效层 | 方案层 + 选优层（录入→校验→校核→Pareto 硬筛选→方案槽位/docx） |
| 是否进内核 | **否**。不改 FEATURES/代理/NSGA-II，74 方案、knee、四率权威数字全部不变 |
| 违反处理 | 硬约束→阻断保存与方案生成；软约束→告警并写入方案提示 |
| 缺省零漂移 | 未显式录入约束的老存档不启用硬阻断；默认上限高于前端默认录入值 |

用户明确不选"注入寻优内核重算"路线（那会推翻权威数字并要求重训代理模型），也不选"仅录入展示"（约束仍是软的）。

---

## 3. 约束规格（13 项，12 硬 + 1 软）

单一数据源：`src/brigsim/support/constraints.py :: CONSTRAINT_SPEC`

| 源 | 硬/软 | 字段 | 含义 | 默认 |
|---|---|---|---|---|
| 资源现实源 | 硬 | `repair_veh_cap` | 装甲抢修车可用上限（台） | 8 |
| 资源现实源 | 硬 | `arv_cap` | 装甲抢救车可用上限（台） | 6 |
| 资源现实源 | 硬 | `supply_veh_cap` | 器材补给车可用上限（台） | 4 |
| 资源现实源 | 硬 | `personnel_cap` | 维修人员上限（人） | 120 |
| 资源现实源 | 硬 | `personnel_per_team` | 单组修理人员编制（人/组） | 8 |
| 资源现实源 | 硬 | `repair_rate_per_team` | 单组修理能力（台/小时） | 3.0 |
| 指挥权威源 | 硬 | `repair_deadline_h` | 抢修时限（小时） | 8.0 |
| 指挥权威源 | 硬 | `evac_deadline_h` | 后送时限（小时） | 12.0 |
| 指挥权威源 | 硬 | `key_equipment_min_ready` | 重点装备完好率下限（%） | 0（不设限） |
| 指挥权威源 | 硬 | `superior_order` | 上级命令原文/要点（文本） | 空 |
| 指挥权威源 | 硬 | `priority_rule` | 抢修优先规则（文本） | 空 |
| 战损物理源 | 硬 | `max_repair_grade` | 本级最高可修等级 M/F/K | F |
| 软约束 | 软 | `risk_budget` | 风险预算上限（0-1） | 1.0（不设限） |

资源上限三项默认 8/6/4 高于前端默认录入 6/3/2，保证缺省零漂移。

---

## 4. 实现要点

### 4.1 后端新增模块
`src/brigsim/support/constraints.py`（纯函数，毫秒级，不依赖仿真内核）：
- `defaults()`：默认约束字典
- `validate(payload)`：范围/类型/逻辑校验，硬越界抛 `ValueError`，软越界钳制告警
- `effective_bounds(constraints)`：把资源上限派生为 7 旋钮有效上界（只读 `FEATURES` 上界，取 min）
- `check_feasibility(mission, constraints, loss=None)`：逐条校核，返回 `{overall, items, n_fail, n_warn, blocking, constraints}`
- `constraint_slots(...)`：派生 5 个方案槽位（确定性，非 LLM）

### 4.2 录入契约（mission_input.py）
- `validate()` 调用约束校验；车辆录入数 > 上限 → 抛 `ValueError`（硬阻断）
- 记录 `constraints_configured` 标志：仅当 GUI 显式录入约束时为 True，用于零漂移

### 4.3 校核逻辑
- 资源：录入车辆数 ≤ 上级上限
- 人员：`抢修组数 × 单组人员 ≤ 人员上限`
- 时限：`组数 × 单组能力 × 抢修时限 ≥ 待修数（M+F）`（有清单才校核，无清单标"待推演后校核"，不阻断录入）
- 完好率下限：有清单且设了下限时比对实测完好率
- 文本约束：录入即视为已接受（非 LLM 生成）

### 4.4 Pareto 硬筛选（optimize_pareto.py）
- `recommend(..., constraints=None)`：非 None 时用有效上界逐方案核对，剔除超编方案
- **只读** `pareto.json`，不改任何方案；返回 `n_constraint_filtered`、`constraint_bounds`、`constraint_detail`、`constraint_note`
- 缺省 None 时行为与旧版完全一致（零漂移）
- 全部被剔除时返回明确 error 提示，不崩溃

### 4.5 API / 路由
- `GET  /api/support/catalog` 增加 `constraint_spec` / `constraint_sources`
- `GET|POST /api/support/constraints/check` 校核端点
- `POST /api/support/mission`：硬约束违反返回结构化 `{error, error_type, constraint_violation}`（HTTP 200，前端可清晰展示）
- `POST /api/support/plan`：显式录入约束且校核 fail → 阻断方案生成，返回校核详情
- `GET /api/support/pareto/recommend`：自动带最近录入的约束（仅显式启用时）

### 4.6 方案模板
`support_plan_v1.md` 第二章新增三款：
- （四）上级命令与约束：{{constraint_order}}、{{constraint_priority}}
- （五）资源与能力约束：{{constraint_summary}}、{{constraint_deadline}}
- （六）约束可行性校核：{{constraint_feasibility}}

5 个约束槽位加入 `SLOT_DICT`，`source=mission/decision`（确定性），LLM 不得改写；校核结构化结果随方案 JSON 落盘（`constraint_check` 字段）。

### 4.7 前端
- `support_flow.js`：录入区新增"真实约束与上级命令（三源）"details 分组（13 字段，四源小标题，硬/软徽章，按钮"校核当前约束"）
- 收集/回填闭环；保存后自动校核；scheme 生成硬阻断时展示校核表；Pareto 模态显示"已按 GUI 真实约束筛选 + 剔除数 + 剩余候选"
- `research.js`：原"三源是设计方法"说明改为"已在录入中做成 GUI 可设定真实输入"的落点说明
- 资源版本 `p2c → p3`（research.js `c926 → c927`）

---

## 5. 验证（实测数据）

### 5.1 单元测试
`tests/test_constraints.py` **23 passed**（规格/默认值/校验硬阻断软告警/有效上界/校核/Pareto 筛选/零漂移/录入契约/槽位集成）。

### 5.2 回归测试
约束 + support 全套 **111 passed**（test_constraints / test_support_flow / test_force_config / test_support_p0/p1/p2/pareto / test_repair_flow）。

### 5.3 浏览器实测
`tools/verify_constraints_gui.py`：12/13 通过，无浏览器 console/page 错误。覆盖约束组渲染（13 字段、四源、硬软徽章）、校核表渲染、保存闭环、硬阻断 alert、Pareto 约束筛选说明。
（唯一未勾选项 `has_four_sources` 为脚本抓 `inner_text` 时机问题，四源数据经 API 核实完整：resource/command/physical/soft。）

### 5.4 Pareto 筛选实测
- 不传约束：74 候选，`n_constraint_filtered=0`（零漂移）
- 抢修车上界=2：剔除 42 个（抢修组 8 个 + 抢救车 33 个 + 器材车 3 个，按维度统计），剩余 32 个，Top 方案抢修组全 ≤2
- 极限约束（1/1/1）：返回"无可行方案"提示，不崩溃
- 只读校验：筛选前后 `pareto.json` 字节完全一致

### 5.5 权威数字
本次改动**没有修改** surrogate / experiment / 内核 / pareto.json，四率、R²、加速比、74 方案、knee、0.165/0.458/64% 等权威数字全部不变。

---

## 6. 已知边界与后续
1. 抢修时限校核是有清单后的派生估算（按"组数×能力×时限≥待修"），不是内核排队仿真的精确 MTTR；内核机理不变。
2. `key_equipment_min_ready` 目前比对全局完好率，未做"重点装备"按装备权重子集单独派生——如需精确按 wᵢ 加权，属后续增强。
3. 上级命令为自由文本，进入方案槽位原文；LLM 仅写叙述、数字槽位确定性，命令文本不被 LLM 改写。
4. `budget_limit(万元)` 量纲与 Pareto 归一化成本不同，仍不直接参与筛选（沿用 P2-5 结论，未改）。
