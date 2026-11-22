# 对战双方实力 GUI 设定 — 功能说明 v1

> 日期：2026-11-21　｜　范围：作战保障全过程「节点①任务与资源录入」新增能力
> 设计红线：**不改仿真内核、不改权威数字**。双方实力缺省时完全零漂移。

## 1. 需求与现状差距

原需求："输入阶段，对战双方的实力都需要能够通过 GUI 进行设定。"

核查发现原实现的三处缺口：

| 缺口 | 原状 | 影响 |
|---|---|---|
| 装备数量未注入编成 | `equipment` 仅映射 3 个保障旋钮（抢修组/抢救车/器材车），26 类装备数量只用于方案 roster 展示 | GUI 改装备数不影响交战 |
| 蓝方无任何录入项 | `run_config` 中 `blue_pk_scale` 硬编码 1.0 | 敌情无法设定 |
| 方别效能不可调 | 无 pk/detect/speed 的录入 | 训练水平/侦察/机动差异无法表达 |

## 2. 实现方案（最小侵入）

新增独立模块 `src/brigsim/support/force_config.py`，在推演加载想定后、`apply_features` 前，用
`Scenario.model_copy(update=...)` 覆盖编成/空中架次/方别乘子——**内核零改动**。

```
GUI forces 录入 → mission_input.validate → 落盘 mission_<id>.json
                                              ↓
_deduce_worker: load_scenario → force_config.inject → apply_features → run_replications
                                              ↓
                        战损清单(forces_report) → 方案(enemy_strength_desc / force_balance_desc)
```

### 2.1 双方对称的录入项

每方（红/蓝）均可设定：

- **地面装备数量**：标准 26 类中 23 类地面平台；并自动兼容想定专属类型（如 `medium_brigade` 的 `wheel_assault/wheel_ifv/wheel_recon/wheel_howitzer/wheel_sam/wheel_ew`）——载入默认时动态生成录入框。
- **空中架次数量**：`uav`（无人机）/ `loitering`（巡飞弹）/ `helo`（武装直升机）。
- **方别效能乘子**：`pk_scale`（交火效能）/ `detect_scale`（侦察效能）/ `speed_scale`（机动速度），默认 1.0，范围 0.1~5.0。
- **带病比例 / 工事比例**：0~1，按比例确定性标记 `initial_worn` / `dug_in`。

### 2.2 数量语义与零漂移

- 给定类型的录入数 = 该方该类型的**目标总数**；缩放下沿用 `design._resize_entity_group` 的"裁尾/克隆顺延"约定。
- 想定中不含某类型时不凭空新增（无锚点几何），在 `forces_report.notes` 中说明。
- **默认不勾选"自定义双方编成"** → `forces={}` → `inject()` 原样返回 scenario，四率、R²=0.705、加速比 38 万倍等权威数字完全不变。

### 2.3 数据来源与叠加约定

- 「载入当前想定默认编成」读取该想定实际编成作为初值，切换想定会先清零避免残留。
- 方别乘子由 GUI 直接覆盖 `SideModifiers`；`apply_features` 的 `red_pk_scale/blue_pk_scale` 情景因子在其上继续累乘（两者叠加，符合既有约定）。

## 3. 关键数据项（forces 契约）

```json
{
  "forces": {
    "red": {
      "equipment": {"tank": 10, "ifv": 32, "wheel_assault": 5},
      "air": {"uav": 2},
      "modifiers": {"pk_scale": 1.2, "detect_scale": 1.0, "speed_scale": 0.9},
      "worn_ratio": 0.1,
      "dug_ratio": 0.0
    },
    "blue": {
      "equipment": {"atgm_team": 5},
      "air": {"loitering": 1},
      "modifiers": {"pk_scale": 1.35, "detect_scale": 0.8, "speed_scale": 1.0},
      "worn_ratio": 0.0,
      "dug_ratio": 0.6
    }
  }
}
```

字段落在既有 `mission_<id>.json`，随录入一并保存；未启用时 `forces` 为空对象。

## 4. 新增/改动清单

| 类别 | 文件 | 说明 |
|---|---|---|
| 新增后端 | `src/brigsim/support/force_config.py` | 校验 / 默认编成读取 / 注入 / 摘要 |
| 改后端 | `support/mission_input.py` | validate 纳入 forces；summary 增加双方兵力字段 |
| 改后端 | `support/api.py` | 目录增加 force_config；`forces_default()`；worker 注入 + forces_report 入清单 |
| 改后端 | `support/catalog.py` | SLOT_DICT 增 enemy_strength_desc / force_balance_desc |
| 改后端 | `support/plan.py` + 模板 | 方案"情况判断"纳入敌我兵力（确定性数字，不经 LLM） |
| 改后端 | `agent/server.py` | 新增 GET `/api/support/forces?scenario=...` |
| 改前端 | `web/js/support_flow.js` | 红蓝 tab、动态专属类型框、载入默认、汇总条、forces 收集/回填 |
| 改前端 | `web/css/support_flow.css` | 双方实力分区样式；版本号 sf05 |
| 新增测试 | `tests/test_force_config.py` | 11 项：对称校验、缩放、乘子、零漂移、专属类型、方案槽位 |
| 新增工具 | `tools/verify_force_config_ui.py` | 浏览器端到端实测脚本 |
| 本文档 | 本文件 | — |

## 5. 验证结果

- **单测**：`test_force_config.py` + `test_support_flow.py` 共 **22 passed**。
- **浏览器端到端**（`tools/verify_force_config_ui.py`，8848 服务）：
  - 红蓝 tab 各 23 个地面装备框、3 个空中框、6 个乘子框；
  - `example_brigade` 载入默认 → 红 119/蓝 45；`medium_brigade` → 红 14/蓝 9，且 `wheel_assault` 专属框自动出现（值 5）；
  - 切换想定无旧值残留；改蓝方 `atgm_team=5`、`pk=1.35`、红方 `tank=10` 后保存 → 2 次推演 → 清单就绪；
  - 方案生成后敌我对比槽位正确显示「红方地面 97 台…蓝方 35 台…1.00 : 1.35」；
  - 数字一致性复核通过（45 个数值槽位）；console 无新增业务报错（仅 rerun 外链 CSP 拦截，属既有离线行为）。

## 6. 操作动线

1. 顶栏「保障全过程」→ 节点①「任务与资源录入」。
2. 「对战双方实力设定」勾选"自定义双方编成"。
3. 选基础工况 → 点「载入当前想定默认编成」（自动填红蓝双方）。
4. 用红/蓝 tab 切换，逐项改数量、乘子、带病/工事比例；顶部汇总条实时显示双方兵力。
5. 保存录入 → 多次推演 → 战损清单 → 方案。方案"情况判断"将显示本次录入的敌我兵力对比。

## 7. 待确认 / 已知边界

- 想定中不存在某平台类型时不凭空新增单位（缺锚点几何）；如需新增类型请换基础想定或后续扩展模板展开能力。
- 带病/工事比例按单位序号确定性分配（前 k 个），非随机抽样；如需随机分布可后续加种子控制。
- 双方实力的敌方部分用于**驱动对抗推演与方案敌情判断**，不改变红方四率的计算口径（仍只统计红方）。
