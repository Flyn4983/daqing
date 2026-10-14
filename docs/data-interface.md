# 数据接口规范（L0）

- 版本：0.1（2026-10-14，比赛 C2 产出）
  入口模板：[templates/scenario_template.yaml](../templates/scenario_template.yaml)；
  参数目录（数据字典+参数域+文献）：[templates/param_catalog.yaml](../templates/param_catalog.yaml)。
  四率口径权威说明见 [src/brigsim/experiment/four_rates.py](../src/brigsim/experiment/four_rates.py)。
- 目标：**代码与数据分离，数据资产可替换、可溯源、可版本化**；用户拿真实数据只改数据文件，不改模型代码。
  GJB 口径对齐全见第 6 节。GJB 451A 术语（S2）：MTBF 等可靠性术语以该标准为口径参照。

## 1. DataProvider 抽象

```python
class DataProvider(Protocol):
    def load_scenario(self, name: str) -> Scenario: ...
```

- 一期实现 `YamlFileProvider(base_dir)`：`<base_dir>/<name>.yaml`（也接受带 `.yaml` 后缀的相对路径）；
- 预留 `DbDataProvider`（SQLite/参数库）、`ApiDataProvider`（数据平台），模型层代码不变；
- 加载即经 Pydantic 校验（[schema.py](../src/brigsim/data/schema.py)）：类型、取值域、模板展开自动完成；
- GeoTIFF/通行性栅格的相对路径以想定 YAML 所在目录为基准解析。

## 2. 输入：想定 YAML 各表（字段 / 单位 / 默认 / 留白）

时间基准 `dt_seconds=30`（1 tick = 30 秒）。物理量与 tick 的换算见第 3 节。

### 2.1 mission 任务表

| 字段 | 类型 | 默认 | 口径与【待填】 |
|---|---|---|---|
| duration_ticks | int | 120 | 任务总时长（tick）。【待填】按实际战法阶段时限 |
| dt_seconds | int | 30 | 时间步长（秒），全系统口径基准，勿轻改 |
| objective | [x,y] | (8000,5000) | 单目标终点（米，战场平面坐标，东 x 北 y） |
| success_radius_m | float | 300 | 夺占成功半径（米）。【待填】按目标地幅 |
| phases | list | None | 多阶段：name/objective/success_radius_m；任一存活红方单位进入半径即切换；给定后优先于 objective |

### 2.2 terrain 地形表

| 字段 | 类型 | 默认 | 口径 |
|---|---|---|---|
| source | synthetic/geotiff | synthetic | synthetic=FBM 程序噪声；geotiff=读 DEM |
| geotiff_path | str/null | None | 【待填真实测绘】相对 YAML 的单波段 GeoTIFF，裁剪为方形战区，双线性重采样到 grid_n，像元线性拉伸到 size_m |
| passability_path | str/null | None | 【待填】通行性栅格 [0,1]（1=道路/硬地，0=不可通行，0.12=无桥河面），与 DEM 同网格配准；与坡度/涉水连乘进入机动修正 |
| size_m | [w,h] | (10000,10000) | 方形战场边长（米） |
| grid_n | int | 65 | 内核网格单边长宽格点数 |
| noise_seed/noise_scale/octaves/base_height/amplitude/water_level | - | 7/3.0/4/60/150/5 | 仅 synthetic 生效的 FBM 参数（米） |

### 2.3 environment 气象表

| 字段 | 类型 | 默认 | 口径 |
|---|---|---|---|
| weather | clear/rain/fog/snow | clear | 【待填】任务时段气象。当前全程常量，**时变气象序列为留白扩展** |
| visibility_factor | float (0,1] | 1.0 | 探测距离修正乘子；同时驱动可视化粒子。【待填】按能见度实测/预报 |

### 2.4 platform_types 平台类型表（每类装备一组）

| 字段 | 单位 | 默认 | 原型值(tank/ifv/atgm) | 公开参照与置信度 |
|---|---|---|---|---|
| sensor_range_m | 米 | 必填 | 2500/1800/2200 | 昼间稳像观瞄对装甲目标 2-4km（S9），0.40 |
| weapon_range_m | 米 | 必填 | 2000/1200/2000 | 120/125mm 动对动 ~2-3km、ATGM 3-5km（S9），0.40 |
| pk | [0,1] | 必填 | 0.35/0.22/0.45 | **无公开权威值，v3 标定值，0.25**；【待填】实弹/演习毁伤数据 |
| speed_m_per_tick | 米/tick | 必填 | 184/217/100 | MBT 最大越野 45-55km/h（S9），原型为含 0.836 地形/接敌减损的平均接敌速度，0.50 |
| reload_ticks | tick | 2 | 2 | 战斗射速 4-6 发/分（S9），0.40 |
| detect_prob | [0,1] | 0.85 | 0.85 | 原型假设，0.25；【待填】观瞄试验数据 |
| track_ttl_ticks | tick | 3 | 3 | 共享航迹老化（C2 抽象），0.20 |
| mtbf_ticks | tick | 400 | 400 | 3.33 战斗小时；M1A1 ~9-10 越野小时(S1)、397-512km(S6)、部件 4000+h(S7)，0.35 |
| mttr_ticks | tick | 6 | 6 | 3 分钟火线抢修抽象；前沿 2-6h(S4)/连级数小时(S3)/战役 2-4 周(S3,S5)，0.35 |
| meta | ParamMeta | example/0.1/0.5 | prototype_calibrated_v3/0.4/0.4 | 来源/版本/置信度/更新日期 |

另有交战代码常量：受损（可修复）概率固定 **0.25**（roll∈[pk, pk+0.25)），M5 弹道升级时参数化（见第 8 节）。

### 2.5 sustainment 保障表（C4 优化主变量区）

| 字段 | 单位 | 默认 | 原型值（旅级） | 口径与【待填】 |
|---|---|---|---|---|
| repair_teams | 组 | {red:1,blue:1} | 6/4 | 可并行抢修组数（战斗受损与自然故障共用队列；战毁不可修）。【待填】修理营/连实际编配 |
| repair_delay_ticks | tick | 0 | 0 | 受损到送修固定延迟（未来改分布抽样） |
| spare_stock | 件/null | null（无限） | 12/8 | 初始备件基数；每次维修耗 1 件，耗尽则维修挂起。真实需求按 MUBR 消耗因子（S8） |
| resupply_interval_ticks | tick | 40 | 20 | 周期前送间隔。【待填】保障梯队实际节奏 |
| resupply_qty | 件 | 4 | 8 | 每周期投送量 |
| repair_station | {side:[x,y]}/null | None（原地修） | [1500,5000]/[9500,5100] | 【待填】修理所实际展开位置；在修单位物理后送至此、修复后再前出 |

### 2.6 side_modifiers 方别乘子表（缺省键=1.0）

`pk_scale / detect_scale / speed_scale`（>0）：乘到该方各平台基准值上，建模既设防御、训练差等方别不对称。
原型 v3 标定 `blue.pk_scale=1.000`，置信度 0.25。

### 2.7 forces / force_templates 编成表

- `forces.<side>`：UnitSpec 列表——unit_id、type（须在 platform_types 中）、pos[x,y]、org（编制链自由文本）、heading_deg（0=北，顺时针）。
- `force_templates.<side>`：成建制模板——type/count/anchor/heading_deg/org/id_prefix/layout(line/column/wedge)/spacing_along/spacing_lateral；加载时自动展开并入 forces（展开后与显式列表等价）。
- 【待填】整张实际编制序列、型号、集结坐标。示例：`example_company`（13 台）、`example_battalion`（44v27）、`example_brigade`（红 104/蓝 63）。

## 3. 时间与单位换算

| 换算 | 公式（dt=30s） |
|---|---|
| 平台速度 | km/h = speed_m_per_tick × 3.6 / dt = ×0.12 |
| MTBF/MTTR/延迟/间隔 | 物理小时 = ticks × dt / 3600 = ticks / 120 |
| 任务时长 | 分钟 = duration_ticks × dt / 60 = ×0.5 |

**尺度边界**：模型时域是单场战斗（约 30 分钟）。战役/战略级修理（2-4 周、60 天，S3/S5）不在时域内；
`mttr_ticks` 只映射火线/连营级拖救抢修（分钟-小时），战役级修理能力建模是后续扩展。

## 4. 参数溯源与置信度

- 每个平台类型带 `meta{source,version,confidence,updated_at}`（Pydantic 强校验，见 [schema.py](../src/brigsim/data/schema.py)）；
- 全部可调参数的机器可读目录在 [param_catalog.yaml](../templates/param_catalog.yaml)：`domain`（C3 代理模型抽样域 / C4 NSGA-II 优化变量上下界）、`prototype`、`confidence`、`sources[]`、`real_data_slot`（留白标记）；
- 置信度分档：**0.80-1.00** 权威标准/官方手册；**0.50-0.79** 官方审计/学术/多源交叉；**0.30-0.49** 单一公开来源或行业报告；**0.00-0.29** 无外部来源的原型/标定值；
- 填入真实数据后：更新数值、把 meta 改为本部队来源与日期、confidence 置 0.8+，并按第 7 节重标定。

## 5. 输出：四张数据表（Parquet/DataFrame）

仿真输出是可视化、四率统计、外部对接的唯一数据来源：

### 5.1 snapshot 快照表（每 tick × 每单位一行）

| 列 | 类型 | 含义 |
|---|---|---|
| tick, phase | int | 时刻、当前任务阶段 |
| unit_id | str | 单位 |
| side | int | 0=红 1=蓝 |
| x, y, heading | float | 位置（米）、朝向（弧度） |
| alive | bool | 存活（false=战毁，不可修） |
| damaged | bool | 受损/故障（可修复，在抢修通道中） |
| under_repair | bool | 在修（含后送机动） |

### 5.2 engagement 交火事件表

`tick / shooter / target / killed / damaged`：每次射击一行；killed=战毁，damaged=可修复受损。

### 5.3 supply 保障摘要表（启用备件供应链时）

`tick / side / spares（库存）/ in_repair（在修数）`。

### 5.4 events 保障事件流表（C1）

`tick(float DES 时刻) / unit / side / type`，type 四种：

- `failure_natural` 自然故障（指数间隔 mtbf_ticks，仅存活且可用单位计时）；
- `damage_combat` 战斗受损登记（交战段登记、tick 末统一入队）；
- `repair_start` 进入抢修通道（计应修；战毁不计；在修期间战毁计应修不计修复）；
- `repair_done` 修复归建。

## 6. 四率口径（GJB 术语对齐，C1 已落地并经单测冻结）

| 指标 | 公式 | 输出键 |
|---|---|---|
| 完好率 A(t) | 可用数（存活 且 未受损 且 不在修）/ 参战总数 | 终值 `readiness_*`；时序平均 `availability_mean_*`；时序列 `availability` |
| 战损率 D(t) | 累计战毁数 / 参战总数 | `attrition_*`；时序列 `attrition` |
| 故障率 λ | 自然故障数 / 累计暴露（可用单位·tick） | `failure_rate_*`；换算每 100 单位·小时 `failure_rate_100h_*`；时序列 `failure_rate` |
| 修复率 R(t) | 修复归建累计 / 应修累计（无应修时记 1.0） | `repair_rate_*`；时序列 `repair_rate` |

配套计数键：`failures_natural_* / failures_combat_* / repairs_required_* / repairs_done_*`（`*`∈{red,blue}）。
任务完成率 `success` 为顶层效能指标，不属四率。
实现：[four_rates.py](../src/brigsim/experiment/four_rates.py)，单测 [test_c1_four_rates.py](../tests/test_c1_four_rates.py)。
> 口径参照 GJB 可靠性/维修性术语系列（S2）的通用定义做原型解释，权威条文以正式标准为准。

## 7. 真实数据替换流程（用户操作）

1. 复制 `templates/scenario_template.yaml` → `scenarios/<想定名>.yaml`；
2. 按表中【待填】标记与 [param_catalog.yaml](../templates/param_catalog.yaml) 的 `real_data_slot=true` 行逐项填真实数据（DEM/通行性文件放 `scenarios/terrain/` 或 data/real/，**涉密不入库**）；
3. 加载校验：`uv run pytest tests/test_c2_data_templates.py`；冒烟：`uv run brigsim run <想定名> --runs 20`；
4. **重标定**：机制或关键参数改变后必须重标（CRN 蒙特卡洛 + scipy 差分进化，参考 M3 v3 流程与 `experiment/calibration.py`），使 success/战损落回参考目标带；
5. 对照四率：对比替换前后完好/战损/故障/修复率分布与置信区间；
6. 版本化：更新 meta.source/version/updated_at/confidence，在 DEVLOG 记录数据来源与残差。

## 8. 留白数据清单（当前为原型，须用户补充）

| 数据 | 现状 | 替换物 |
|---|---|---|
| 测绘 DEM / 通行性 | 程序噪声或合成峡谷 GeoTIFF | 真实方形战区配准双栅格 GeoTIFF |
| 编制 ORBAT | 示例 13/71/167 台 | 实际编制序列、型号、坐标 |
| pk / detect_prob | v3 标定（0.25/0.25 置信度） | 实弹射击、演训毁伤与发现数据 |
| MTBF/MTTR | tick 原型（0.35） | 本部队故障/修理登记（按 S2 术语统计） |
| 修理所坐标/抢修组数 | 原型坐标与编配（0.20） | 实际保障部署 |
| 备件基数/前送 | v3 网格标定值 | 战备基数标准、MUBR 消耗因子（S8） |
| 气象 | 四类常量 | 任务时段时变能见度序列 |
| 分部件维修 | 统一 MTTR、每件耗 1 备件 | 部件 MTTR 表、备件 SKU 清单（M5 弹道/部件毁伤配套） |

## 9. 未来扩展：弹道 / 落点 / 落角 / 毁伤（M5 候选，预留未实现）

当前交战为聚合概率模型（射程内一次抽样：击毁 pk / 可修复受损 0.25 / 否则脱靶），无弹丸飞行、落点散布、落角、穿深。
`param_catalog.yaml` 已**预留弹道参数槽**（`future_extensions.ballistics`，当前内核不读取）：
初速、千米 CEP、射程-落角表、部件毁伤表；拟采用等效解析形式

```
P_kill = P_hit(距离, CEP, 能见度) × Σ部件 P(部件|命中, 落角) × P_kill(部件, 装甲)
```

兼容 tick 制，且部件级毁伤直接对接分部件 MTTR/备件。高保真六自由度外弹道经评估不适合蒙特卡洛大样本主线，暂不采用。
开源调研方向：OneSAF 交战建模、JMASS、公开穿甲数据汇编。

## 10. 公开来源（参数整编依据）

| 编号 | 来源 | 用途 |
|---|---|---|
| S1 | POGO《The Army's M1 Tank》(1990，引陆军 RAM-D) | MMBCF、维修工时率 |
| S2 | GJB 451A-2005 可靠性维修性保障性术语 | 四率/MTBF 术语口径 |
| S3 | Ukraine War Analytics 2026 坦克修理能力 | 抢修时长分级、50% 返建 |
| S4 | DefStrat《The Cost of Poor Sustainment》 | 前沿 2-6h 返建、修复比例 |
| S5 | U.S. AFCENT BDRF Stryker 修理设施 | 战役级 60 天修理口径 |
| S6 | 中国装甲车服务行业报告（行业预测，低可信） | 出动强度、MTBF 里程、完好率 |
| S7 | RCM3 装甲车辆冷却系统论文 | 部件级 MTBF |
| S8 | GAO-01-425 陆军战储备件 | MUBR 消耗因子、备件需求分布 |
| S9 | Leopard 2 等公开装备规格 | 越野速度、射程、观瞄距离 |

链接见 [param_catalog.yaml](../templates/param_catalog.yaml) 的 `sources` 段。
