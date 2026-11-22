# 成本约束 Pareto 选优（P2 边界补齐）实现说明 v1

> 补齐目标：P2-5 已能录入三维成本权重，但过去只填进方案文字、未真正参与"给定成本约束→反推最优保障参数"。本批次把权重落到既有 Pareto 前沿 74 个方案上做可解释加权评分，闭合核心反问题叙事。
> 内核 / `experiment/` / `surrogate/` / `rerun_view.py` / 权威数字（R²=0.705、加速比≈38 万倍、Pareto 74 方案、knee 成本 0.165、四率口径）**零改动**；不重跑仿真；只读 `pareto.json`。
> 完成日期：2026-11-22。资源版本 `p2b`。

---

## 0. 结论摘要

新增独立只读派生模块 `src/brigsim/support/optimize_pareto.py`，以及两个 API 端点与一个前端交互入口。**没有新训练、没有新寻优、没有改任何权威数据文件**：所有评分都基于 `pareto.json` 中已有的 74 个方案及其 9 项指标，只是按录入权重重新排序。这让"给定成本约束反推最优保障参数"这一反问题在界面上可现场演示，同时不触碰任何受保护数字。

核心边界（务必明确）：结论是**既有 Pareto 前沿内按偏好的加权排序，不是新的权威结论**。它回答"在这 74 个已寻优方案里，按我现在的偏好哪个最合适"，而非"重新算出一个更优解"。

---

## 1. 功能清单

| 项 | 后端 | 前端 | 实测 |
|---|---|---|---|
| 三维加权评分 | `optimize_pareto.recommend`：战损(`attrition_red`,越低越好)/时效(`readiness_red`,越高越好)/成本(`cost`,越低越好) 各做 min-max 归一到[0,1]，再按权重求和 | 选优模态显示归一后权重、评分公式、锚点对照 | 成本权重极高→选中 cost=0.016；战损权重极高→选中 attr=0.2346；时效权重极高→选中 readiness=0.5971 |
| 成本硬约束 | `cost_ceiling`（0-1 归一化，与方案 cost 同量纲）先过滤再排序 | 模态内「成本上限」输入 + 应用上限；显示已过滤数 | 上限 0.2 过滤 41 个、top 方案成本均≤0.2；上限 0.15 浏览器实测通过 |
| 锚点对照 | 同权重下计算 leanest/knee/strongest/baseline 的评分并列 | 模态内四张锚点卡 | knee 成本 0.165 原值不变、评分随权重变化 |
| 一键采用 | `POST /api/support/pareto/apply`：把选中方案 7 维决策写入新方案的 `pareto_selection` 附节字段（另存不覆盖） | 方案栏附节框：推荐参数/评分/权重/来源；点「采用」后自动拉取新方案并渲染 | 采用后方案号更新为 PQ-xxxx、附节正确显示、原稿保留 |
| 权重回退 | `GET /api/support/pareto/recommend`：weights 缺省时回退最近录入 mission.cost_weights，再缺省等权 | 前端优先用当前表单权重，未录入时交给后端回退 | 不传 weights 不报错、有效权重归一到 1 |

---

## 2. 评分方法（可解释、可复核）

对全部 74 方案，每个维度先算 min-max 区间，再归一：

```
s_战损 = (max(attrition_red) - a) / (max - min)      # 越低越好 → 反向
s_时效 = (r - min(readiness_red)) / (max - min)       # 越高越好 → 正向
s_成本 = (max(cost) - c) / (max - min)                # 越低越好 → 反向
score  = w_战损·s_战损 + w_时效·s_时效 + w_成本·s_成本
```

- 权重来自 `mission_input` 已归一的 `cost_weights`（三项和为 1）；未归一时在本模块再次归一。
- `cost_ceiling` 与方案 `cost` 同为 0-1 归一化成本，作为硬约束先过滤；`0`/空/`>1`/非法值均视为"不设限"。
- **budget_limit（万元）不直接参与筛选**：P2-5 录入的该项单位是万元，而 Pareto 的 cost 是无量纲归一化值（人力/车辆 0.45 + 器材 0.30 + 波次 0.25），量纲不一致。界面与接口都如实提示，杜绝虚构换算关系；如需按金额约束须人工换算为归一化 `cost_ceiling`。

---

## 3. 只读性与红线守卫

- `pareto.json` 全程只读：`recommend()` 仅 `read_text`，`apply()` 只写方案 JSON（`outputs/support/plans/`），从不写回 `src/brigsim/agent/web/data/`。
- 单测 `test_pareto_json_never_modified` 用 md5 前后比对证明文件字节未变。
- 内核、`experiment/`、`surrogate/`、`rerun_view.py` 一行未改；不重跑仿真、不重做 NSGA-II 寻优。
- 权威数字全部保持：Pareto 仍 74 方案、knee 成本仍 0.165、R²=0.705、加速比≈38 万倍、四率口径不变。
- 选优结论落在方案的独立字段 `pareto_selection`，**不进入数字槽位、不被 LLM 改写、不污染正文模板**，因此数字一致性复核逻辑完全不受影响。

---

## 4. 验证结果

- **后端单测**：`tests/test_support_pareto.py` 13 项全过（权重归一、三类权重单调性、成本硬约束、锚点、量纲诚实、只读 md5、API 回退、apply 不污染槽位）。
- **回归**：support 全套 56 passed；C7 可视化相关 42 passed；全量（上一批次基线）273 passed / 3 skipped。
- **浏览器端到端**：`tools/verify_support_pareto.py` 18 项检查全通过、0 控制台错误（按钮/模态/公式/锚点/Top8 表/成本上限应用与过滤/一键采用/附节渲染/方案号更新）；截图 `tmp_rr/pareto_final.png` 布局无错位溢出。
- 8848 大屏服务已重启加载新代码，新端点 `GET /api/support/pareto/recommend` 返回 74 方案评分结果。

---

## 5. 已知边界

1. 评分维度用 `attrition_red`（红方战损率）与 `readiness_red`（红方完好率）代表"战损/时效"，与四率中的蓝方战损、修复率等不是一一对应；这是 Pareto 方案已落盘字段的客观约束，若要换维度需重新生成 pareto 数据（超出只读边界）。
2. 排序是线性加权，不引入新的偏好结构（如拍卖/理想点距离）；刻意保持可解释、可复核。
3. `budget_limit`（万元）仍只作展示提示，未与金额换算打通（量纲诚实，见 §2）。
4. 一键采用写入的是"建议参数附节"，**不会自动回写内核 7 旋钮重新推演**——那会触发仿真、触碰权威数字红线；如需据此重算，须显式走"多次推演"并另行评估数字一致性。

---

## 6. 改动文件

- 新增：`src/brigsim/support/optimize_pareto.py`、`tests/test_support_pareto.py`、`tools/verify_support_pareto.py`、本文档。
- 修改：`src/brigsim/support/api.py`（导入 + 2 端点）、`src/brigsim/agent/server.py`（GET/POST 各 1 路由）、前端 `index.html` / `support_flow.js` / `support_flow.css`（按钮 + 模态 + 附节 + 样式，版本 p2a→p2b）。
