# brigsim · 合成旅作战任务仿真

模拟合成旅在特定任务、环境、地形条件下的作战过程，通过蒙特卡洛多次模拟，
统计 **装备"四率"（完好率/战损率/故障率/修复率，GJB 口径）与任务完成率** 等指标及其分布。

技术路线：**实体级交战模型（ABM）＋ 装备保障离散事件模型（DES）＋ 蒙特卡洛批量复制**。

## 当前进度

- [x] **M0 框架骨架**：分层目录、数据接口（DataProvider）、内核抽象、想定 schema、端到端 hello-sim
- [x] **M1.5 可视化提前**：rerun 3D 态势回放（轨迹/交火/目标点）+ 战损完好率曲线
- [x] **M1 最小闭环**：Mesa 交战（侦察/C2/机动/交战四阶段）+ SimPy 故障-抢修耦合，三大指标含 95% 置信区间
- [~] **M3 合成旅规模**：SALib Morris 灵敏度 + Sobol 方差分解（S1/ST）、ORBAT 成建制模板（横队/纵队/前三角）营级（44v27）与旅级想定（104v63，双梯队进攻 vs 两线防御）、多阶段进攻任务（出发阵地→突破→夺占纵深要点）、GeoTIFF 双栅格地形接入（DEM + 通行性：道路/桥梁/无桥河面，合成峡谷已上线，真实测绘文件可同名替换）、SimPy 备件供应链（库存约束 + 周期补给，库存/在修曲线已进回放；在修单位后送脱离接触并**物理机动至修理所、修复后再前出**，fill_rate/缺货等待进指标）；**参数标定已落地**（CRN 蒙特卡洛 + scipy 差分进化，开放参考目标带：success 0.50–0.75、红战损 0.45–0.70、蓝战损 0.50–0.85；机制每改必重标，当前 v3 标定 success 0.52、红 0.61、蓝 0.84，备件 fill 红 0.89/蓝 0.93；方别战斗乘子 side_modifiers 建模既设防御不对称）；灵敏度因子已方别化（5→8 因子：red/blue 的 pk·detect·speed 分开 + mtbf + visibility，对称抵消消除——v3 基线 N=128×reps8 时 blue_pk 对红战损 S1=+0.31/ST=0.42，与 red_speed（0.40/0.53）近乎共同主导）；Sobol 已做到 N=128×reps8（v3 基线，排序稳健，再压半宽需增大 N 而非 reps）；待办：真实测绘 DEM 同名替换、真实保障数据（修理所坐标/后送速度/修理周期）替换原型假设
- [~] **M4 GPU 后端**：Warp 向量化交战内核——单环境 CPU 比 Mesa 快约 17×
  （旅级 1.38s→0.08s/复制），env 维批量 E=448 时 GPU 9.9ms/复制（约 140×），
  批量与单环境同种子逐轨迹一致；切片 3 支持 per-env 参数列，Sobol 设计点×
  复制可整体平铺上 GPU（全量 448 点对拍 max diff 0.0，旅级 1792 次 GPU 45s）；
  HashGrid 邻域查询替换 sense/engage 的 O(n²) 扫描（蓝方追击因 C2 无界航迹
  保留稠密；当前规模吞吐持平，复杂度降为 O(n·k)）；
  切片 5 CUDA 图捕获：每步设备序列（9 launch + 2 次 grid.build）录制成图
  replay（tick 经单元素设备数组传入），E=448 10.8→9.4ms/复制（约 12%）；
  修复机动内核跨线程位置读写竞争（双缓冲快照），GPU 轨迹逐位可复现；
  切片 6 画像 host 开销（cProfile：SimPy 事件步进 ~67%、阶段判定 ~11%、
  GPU 仅 8%），阶段判定向量化、战损登记去重；
  切片 7 实测 4GB 显存边界：E 到 8192（137 万并发单位）GPU 增量仅 172MB，
  显存非约束，host RSS（~0.23MB/E）与每批 ~1.5s 固定构造才是开销，
  默认 batch_size 128→512；
  SimPy 保障留 CPU tick 边界同步；
  待办：host 侧自研向量化 DES 经评估暂缓（与开源优先冲突、
  当前 9.4ms/复制可接受）

## 快速开始

```powershell
uv venv --python 3.12
uv pip install -e ".[dev,viz]"
uv run brigsim run scenarios/example_company.yaml --runs 100
uv run brigsim run example_battalion --runs 40        # 营级 44v27 三阶段进攻
uv run brigsim run example_brigade --runs 100 --kernel warp   # Warp 向量化内核（自动用 GPU）
uv run brigsim replay scenarios/example_company.yaml   # 打开 rerun 3D 回放
uv run pytest
```

## 分层架构

| 层 | 目录 | 职责 | 主要开源组件 |
|---|---|---|---|
| L4 应用 | `brigsim/cli.py` | 想定加载、跑批、报告 | Typer |
| L3 可视化 | `brigsim/viz/` | 态势回放、指标看板 | rerun-sdk（M1.5） |
| L2 实验 | `brigsim/experiment/` | 蒙特卡洛复制、指标统计 | pandas / SALib |
| L1 内核 | `brigsim/kernel combat sustainment/` | 交战 ABM ＋ 保障 DES | Mesa + SimPy；Warp 向量化后端（M4 切片 1） |
| L0 数据 | `brigsim/data/` | 想定/装备/地形参数资产 | Pydantic + YAML，Provider 可替换 |

## 数据接口（核心约定）

- 所有参数经 `DataProvider` 进入仿真，一期 `YamlFileProvider`，后续可加数据库/API 实现；
- 参数 schema 见 `src/brigsim/data/schema.py`，新增数据资产只需加数据文件，不改模型代码；
- 真实/涉密参数放 `data/real/`（已 gitignore），仓库只保留示例数据；
- **填真实数据从模板开始**：[templates/scenario_template.yaml](templates/scenario_template.yaml)（逐参数单位/公开参考/【待填】），
  参数域与开源文献整编见 [templates/param_catalog.yaml](templates/param_catalog.yaml)；
- 接口说明见 [docs/data-interface.md](docs/data-interface.md)（输入 7 表 + 输出 4 表 + 四率 GJB 口径 + 替换流程）。

## 协作约定

- 开发走 `feature/xxx` 分支 + PR，`main` 保持可运行；
- 每次开发记录追加到 [DEVLOG.md](DEVLOG.md)；
- **新模块开工前先检索 GitHub/Gitee 开源资源**，选型结论写入 DEVLOG，不重复造轮子。
