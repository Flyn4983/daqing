# 开发日志 DEVLOG

> 约定：每次开发追加条目，格式 `## 日期 | 作者 | 主题`；新模块必须记录开源选型结论（复用了什么、为何不自研）。

## 2026-10-13 | trae + 用户 | M0 框架骨架

### 完成内容
- 建立五层骨架：L0 数据 / L1 内核（combat + sustainment）/ L2 实验 / L3 可视化 / L4 CLI
- L0：Pydantic 想定 schema + `DataProvider` 抽象（YAML 文件实现，预留 DB/API）
- L1：`Kernel` 抽象接口 + numpy 结构化状态（ECS 列存，为 Warp GPU 后端保留同一状态布局）
- L1 粗内核：`ToyNumpyKernel` 机动-侦察-射击-毁伤闭环（M1 替换为 Mesa + SimPy）
- L2：蒙特卡洛批量 runner + 三大指标（战损率/任务完成率/完好率）
- L4：`brigsim run` CLI；示例连级想定 `scenarios/example_company.yaml`
- 输出统一事件日志（snapshot/engagement → Parquet），供 M1.5 rerun 回放直接消费
- pytest 冒烟测试、ruff、GitHub Actions CI

### 开源选型记录（先检索后使用，不造轮子）
- 多智能体：**Mesa**（MIT，M1 引入）；离散事件：**SimPy**（MIT，M1 引入）
- 数据校验：**Pydantic v2**；实验统计：pandas/pyarrow；灵敏度：**SALib**（M3）
- 可视化：**rerun-sdk**（Apache-2.0，Rust 内核，适合大规模实体时空回放，M1.5 引入）
- GPU：**NVIDIA Warp 1.12**（已装，本机 GTX 1650 Ti 4GB 验证 cuda:0 可用，M4 批量复制后端）
- 兵棋/ HLA 后备：Open-DIS、CERTI（当前阶段不需要）
- 检索结论：GitHub/Gitee 无现成开源合成旅完整模型，核心参数库涉密且不开源，故只复用框架组件、模型与数据自建

### 环境
- uv 管理 Python 3.12.12（系统默认 3.9 仅用于全局 warp，不用于本项目）
- 远程仓库 https://github.com/Flyn4983/daqing

### 下一步
- M1：Mesa 重写交战（侦察/通信/C2 规则）、SimPy 故障-抢修-备件接入、指标置信区间

## 2026-10-13 | trae + 用户 | M1.5 可视化提前：rerun 3D 回放

### 完成内容
- 安装 rerun-sdk 0.39（含 rerun.exe CLI/native viewer；注意 pip 包不再支持 SDK 内 spawn）
- `viz/rerun_view.py`：读统一事件日志，绘制
  - 3D 轨迹（红/蓝着色、受损黄、击毁灰）、交火箭头（击毁红/致损黄/未中灰）、绿色目标信标
  - 红蓝战损率/完好率随 tick 的时间曲线
  - 自定义蓝图：深色背景大 3D 斜视视图 + 下方指标标签页 + 时间轴
- CLI 新增 `brigsim replay [--seed] [--rrd] [--show/--no-show]`：
  录制 .rrd 并自动调用同环境 rerun.exe 打开
- 已在本机验证窗口观感（GTX 1650 Ti 4GB，120 tick 流畅）

### 开源选型记录
- 沿用前期选型 rerun-sdk（Apache-2.0）；0.39 蓝图 API（Spatial3DView/TimeSeriesView/EyeControls3D）
  原生满足"流畅漂亮"要求，未自研任何渲染

### 下一步
- M1：Mesa 重写交战内核（保持事件日志 schema 不变，可视化零改动）
- SimPy 保障模型耦合，完好率曲线即可反映抢修效果
