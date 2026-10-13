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

## 2026-10-13 | trae + 用户 | M2 写实 3D 可视化

### 完成内容
- 写实地形：`kernel/terrain.py` OpenSimplex FBM 高程场（坡度影响机动、水面减速）；
  可视化按 `height_at` 4× 细分构建 257² 视觉网格（高程/坡度/水域分层配色：
  水面/沙滩/草地/林地/岩石/积雪），半透明水面
- 写实装备：`viz/models.py` 用 trimesh 按真实比例程序化生成 GLB
  （坦克：车体/倾斜装甲/履带/负重轮/炮塔/炮管；步战；反坦克组），顶点色 PBR；
  `ensure_model()` 同名替换机制——M3 可用真实扫描 GLB 直接替换
- 战术要素：中文编制层级标签（引脚引线悬于车顶）、±10° 射界扇面、
  目标信标、交战轨迹箭头（击毁红/致损黄/未中灰）、右侧"交战统计"TextLog
  （CRITICAL/WARN/INFO 分级）、每 10 tick 双方存活/完好播报、红蓝战损曲线
- 毁伤可视化：受损暖灰着色 + 棕色小烟柱，击毁暗化 + 白色升腾烟团串
- 气象：晴/雨/雪/雾四类；雨幕/雪点下落动画，贴地漂移雾团（能见度系数同时影响内核射程）
- schema 扩展：Terrain（噪声参数）、Environment（weather/visibility_factor）、
  UnitSpec（org 编制标签、heading_deg 朝向）；事件日志 snapshot 增加 heading
- 想定 `example_company.yaml` 标注中文编制名与初始朝向

### 设计借鉴（思想借鉴，素材全部程序生成/开源）
- 布局：VBS4 / VR-Vantage 式深色斜视主视图 + 右侧信息面板 + 底部时间轴
- 配色：NATO/兵棋惯例红攻蓝守、暗色背景；OneSAF AAR 的时间序列 + 事件流复盘布局
- 标签：VBS 引脚式单位标注（竖引线 + 编制名），射界扇面参考 Steel Beasts 武器覆盖扇区

### 开源选型记录
- 地形噪声：**opensimplex**（MIT，纯 Python，无平台依赖）
- 装备模型：**trimesh**（MIT）程序化生成 GLB，不引入重资产管线；渲染仍由 rerun 负责
- 半透明粒子：rerun 0.39 的 Points3D/Ellipsoids3D 带 alpha 会渲染成黑色，
  烟雾/雾改用 trimesh icosphere 实例化 Mesh3D（vertex_colors alpha 混合正常）

### rerun 0.39 踩坑记录
- `albedo_factor` 必须传打包整数 `rr.encodings.Rgba32(0xRRGGBBAA)`；
  0–1 浮点元组会被按 0–255 量化成近黑色（`rerun.datatypes` 已废弃）
- Points3D / Ellipsoids3D 的 RGBA alpha 在该版本上发黑/退化为线框；
  半透明效果一律走 Mesh3D
- 视觉地形必须与 `height_at` 同一插值曲面，否则粗网格三角面与双线性插值面
  相差数米，战车会被埋入地下
- pip 包无独立 viewer，仍用同 venv 的 rerun.exe 打开 .rrd

### 验证
- `ruff check`、`pytest` 全绿；全景/中景/单车特写/雨/雾多组镜头人工截图确认
- 120 tick、257² 地形网格在 GTX 1650 Ti 4GB 上回放流畅

### 下一步
- M1：Mesa 重写交战内核、SimPy 故障-抢修-备件（事件日志/可视化 schema 保持不变）
- M3：GeoTIFF 真实地形接入（沿用 HeightField 接口）、参数标定、旅级规模

