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

## 2026-10-13 | trae + 用户 | M2.1 写实资产升级：地表纹理 + PBR 悍马

### 完成内容
- 地形细节：程序生成 1024² OpenSimplex 双尺度泥地斑驳灰度纹理（UV 0..1 覆盖
  全战场），配合顶点法线（中心差分）产生坡向方向光明暗；纹理与高程分层顶点色
  相乘，草地/林地/岩石/积雪配色保留，地表不再是塑料平面
- 装备升级：步战（ifv）替换为 CesiumJS 示例资产 GroundVehicle（6×6 悍马，
  PBR 沙褐迷彩贴图），编队近景截图确认车体写实质感、接地与朝向正确
- 同名替换机制升级：`ground_offset(type)` 读取 GLB 实际世界包围盒（含节点变换）
  自动计算接地高度并缓存，任意新素材替换文件即可，放置代码零改动
- 坦克（tank）、反坦克组（atgm_team）暂保留程序模型，待真实/专有素材替换

### 资产来源与加工（可复现）
- 源：CesiumJS 仓库 `Apps/SampleData/models/GroundVehicle/GroundVehicle.glb`
  （Apache-2.0，6.1MB；原始下载留存 assets/models/_inbox/，已 gitignore）
- 加工脚本：`outputs/build_humvee.py`
  - 删除 GroundVehicleMesh / _1 两个低 LOD 车身（rerun 不识别 Cesium LOD
    扩展，会三重叠加）
  - 根节点写入单一矩阵：Y-up/+z 车头 → z-up/+x 车头，再按世界包围盒接地
    （初版误用 scene.apply_transform 导致几何与节点双重变换、车体悬空 3.7m，
    改为只改 scene graph 后修复；最终世界包围盒 7.26×2.67，底面 z=0）
  - 材质强制 metallicFactor=0 / roughnessFactor=0.9：glTF 默认金属度 1 在
    rerun 无环境贴图时车身只反射暗色天空、渲染近黑，哑光漆面后贴图正常

### 用户指示
- 本项目为原型软件，专有军仿软件（VBS4 / Steel Beasts 等）的素材允许直接使用；
  用户如有本机素材包可直接放入 assets/models 同名替换

### 验证
- ruff check / format、pytest 全绿；近景截图 outputs/shot_h9.png（沙褐 6×6
  纹理清晰、四轮可见、贴地、朝向正确）；GTX 1650 Ti 回放流畅

### 下一步
- 等待/接入用户提供的写实坦克与反坦克组素材（GLB/FBX/OBJ 均可，trimesh 转换）
- M1：Mesa 重写交战内核、SimPy 保障模型

## 2026-10-13 | trae + 用户 | M2.2 坦克资产替换：Quaternius Tank2（CC0）

### 完成内容
- tank 由程序方块模型替换为 Quaternius Animated Tanks 包的 Tank2
  （低矮车身+圆炮塔+长炮管，苏系 MBT 剪影；加工后 9.8×5.59×2.56m）
- 4 个候选（Tank/Tank2/Tank3/Tank4）全部下载隔离对比，Tank2 高度比例最真实；
  iso 同屏截图确认军绿涂装、履带负重轮、炮塔炮管、接地与朝向（炮口 +X）
- 反坦克组（atgm_team）无开源直链素材，保留程序三脚架导弹模型

### 素材渠道调研（可直链，无需登录）
- GitHub 代码搜索、OGA、poly.pizza、Kenney：写实坦克 glb 基本没有可直链的，
  Quaternius 官网/itch 本机超时，Sketchfab/TurboSquid/CGTrader 写实模型均需登录
- 最终渠道：`trebeljahr/quaternius-showcase`（Next.js 镜像站仓库，CC0，
  含全部 Quaternius 包的单文件 glb），经 jsDelivr CDN 直连下载
  （raw.githubusercontent.com 本机被重置，api.github.com 与 jsdelivr 可通）
- glbxz.com 有 T-72B3/豹2A4/96A 写实模型但为金币/登录下载站，放弃
- 写实级素材的现实路径：用户提供本机专有素材，或提供 Sketchfab 账号 API token

### 加工踩坑（outputs/build_tank.py，顶点色烘焙方案）
- 源 glb 无贴图，PBR 基色被压到近黑（0.06–0.18）；调亮 baseColorFactor 后
  rerun 无环境光，侧立面仍近黑，emissive 自发光与 doubleSided 均未解决
- trimesh 的 PBRMaterial.baseColorFactor 存在 0-1/0-255 量纲混乱
- 最终方案：放弃 PBR，按材质名（Main/Main_Dark/Main_Light/Wheels 五档）
  烘焙为 glTF COLOR_0 顶点色，并按顶点法线 z 分量烘焙 0.75–1.0 的顶光明暗；
  与程序模型走同一渲染路径，rerun 显示稳定，明暗层次保留
- 几何管线：缩放（按炮管向前长度归一到 9.8m）+ Y-up→Z-up 纯旋转矩阵
  （det=+1，避免镜像翻转面缠绕）+ 按世界包围盒接地

### 验证
- ruff、pytest 全绿；三候选同屏 iso 截图（outputs/shot_t5.png）军绿渲染正常
- ground_offset 机制对新 tank.glb 零改动自动适配

### 下一步
- 写实素材待用户输入（本机专有包 / Sketchfab token）
- M1：Mesa 重写交战内核、SimPy 保障模型

## 2026-10-13 | trae + 用户 | M1 交战内核（Mesa）+ 装备保障（SimPy）

### 完成内容
- 新内核 `combat/mesa_kernel.py`（Mesa 3.5.1），切换为 runner/replay 默认内核；
  `ToyNumpyKernel` 保留作粗内核对照
  - **列存为唯一真相**：SimState numpy 列不变式，`PlatformAgent(mesa.Agent)` 仅持
    列索引的薄行为壳，不自带位置/血量，M4 可直接换 Warp 后端
  - 每 tick 四阶段：sense（距离线性衰减探测概率 + 能见度系数）→ 按方共享航迹
    （C2，TTL 过期）→ 机动决策（红方冲击目标点、蓝方前出接敌/固守，受损半速，
    在修不动）→ 交战（reload_ticks 装填冷却、pk 击毁/pk+0.25 致损/否则脱靶）
  - 阶段内固定索引顺序激活，随机性全部来自 Kernel.rng（同种子事件日志逐字节一致）
- 保障模型 `sustainment/repair.py`（SimPy 4.1.2，时间单位=tick，tick 边界与内核
  同步）：每方一个抢修组 Resource；战损送修（延迟→排队→指数 MTTR→归建）+
  自然故障指数 MTBF 循环；战毁不可修；无抢修组（容量 0）则永久排队
- schema/state 扩展：PlatformTypeSpec 增 reload_ticks/detect_prob/track_ttl_ticks/
  mtbf/mttr（默认值，旧 YAML 零改动兼容）；新增 SustainmentSpec；SimState 增
  cooldown/under_repair 列
- 事件日志 schema 完全不变，rerun 可视化零改动，`replay` 用新内核直接录制成功

### 开源选型记录
- 直接落地 M0 选型：Mesa（MIT，ABM 框架，Agent/AgentSet 行为组织）、
  SimPy（MIT，DES 进程/Resource）；未自研任何调度/随机数设施
- Mesa 3.5 集成踩坑：
  1. `Model` 自带实验性 `scenario` 属性，setter 会回写 `scenario.model=self`，
     与 pydantic Scenario 冲突 → 在 MesaKernel 覆盖同名 property
  2. `Model.__init__()` 无参时会塞入 mesa 自己的默认 Scenario 覆盖我方对象 →
     必须先 mesa init 再 Kernel init
- 环境：mesa 3.5.1 依赖 networkx（uv pip 补装 3.7）；venv 无 pip 模块，
  统一用 `uv pip install --python .venv\Scripts\python.exe`

### 验证
- pytest 7 项全绿（M0 冒烟 + M1：装填间隔、同种子确定性、在修不动、
  战损修复、故障-修复循环、无抢修组不归建）；ruff 全绿
- 示例想定 50 复制：success 0.48±0.14、红战损 0.73、蓝战损 0.62
  （toy 对照 0.30/0.90/0.72；新内核因探测概率与装填冷却杀伤略低，分布合理；
  参数平衡留待 M3 标定）

### 下一步
- M3：参数标定与灵敏度（SALib）、真实地形 GeoTIFF；备件供应链扩展
- 写实素材仍待用户输入（本机专有包 / Sketchfab token）

## 2026-10-13 | trae + 用户 | M2.3 步兵素材：three.js Soldier（MIT）蒙皮烘焙

### 完成内容
- 反坦克组（atgm_team）由程序三脚架模型替换为持枪士兵静态模型
  （three.js 官方示例 Soldier.glb，MIT；全装具贴图、1.8m 真实身高、
  Idle 持枪姿态），隔离场景截图确认贴图无黑面、双脚接地、朝向正确
  （Y-up 转 Z-up 后面朝 -Y，与蓝军 heading_deg=270 一致）
- 许可清单：新增 `assets/models/CREDITS.md`，登记三种入库素材的协议
  （tank=CC0、ifv=Apache-2.0、atgm_team=MIT 全文），满足 MIT 保留声明要求
- 用新资产重新录制 outputs/m1_replay.rrd，回放与 ground_offset 机制零改动适配

### 开源选型与素材渠道
- 渠道：three.js 仓库 `examples/models/gltf/Soldier.glb`（2.16MB），
  经 jsDelivr CDN 直链下载（raw.githubusercontent.com 本机仍被重置）
- 写实坦克直链渠道本轮仍无收获：CGTrader/Sketchfab 写实件均需登录，
  Kenney/OGA/poly.pizza 无写实坦克；现实路径仍为用户提供本机专有素材
  或 Sketchfab API token（用户已确认原型阶段可直接使用专有软件素材）

### 关键加工：glTF 2.0 蒙皮烘焙（outputs/bake_skin.py，可复现）
- rerun 不做角色蒙皮、trimesh 不评估 skin，手工实现线性混合蒙皮
  `u_j = inv(G_mesh)·G_joint(t)·IBM_j`，动画采样用四元数 slerp
  （Idle 动画 t=1.0s，避开首帧恰为绑定 T-pose 的问题）
- 四个规范坑：
  1. JOINTS_0 存的是 skin.joints 数组的 slot 索引，不是节点号
  2. IBM MAT4 为列主序：reshape(-1,4,4) 后必须 transpose(0,2,1)
     （REST 姿态偏差由 24606 降到 ~2e-5 验证公式正确）
  3. JOINTS_0/WEIGHTS_0 共用 byteStride=32 的交错 bufferView
     （JOINTS 行内偏移 0，WEIGHTS 偏移 16），read_accessor 增 stride 支持
  4. 烘焙出的世界顶点必须回写为网格 POSITION（漏写导致导出包围盒不变）
- build_infantry.py：按身高 1.8m 定标，Y-up→Z-up 纯旋转（det=+1），
  按几何最低点平移接地（最终 0.768×0.884×1.80m，min z=0）；
  材质保留贴图 UV，强制 metallic=0/roughness=0.9/doubleSided

### 验证
- pytest 9 项全绿、ruff 全绿；iso_atgm.rrd 隔离场景截图目检
  （rerun 单实例会把新 rrd 转发给旧进程，验证前须先 kill 旧 rerun.exe）

## 2026-10-13 | trae + 用户 | M3 灵敏度分析首版（SALib Morris）

### 完成内容
- `experiment/sensitivity.py`：SALib 1.6.0 Morris 一次一列法全局灵敏度
  - 5 个全局乘子因子：pk_scale / detect_scale / speed_scale /
    mtbf_scale / visibility（界别 4，60 设计点 = 12 轨迹×6 点）
  - 每设计点 10 次蒙特卡洛复制取均值，公共随机数（CRN）降噪；
    输出 μ*（总效应）、σ（非线性/交互）、μ* 置信区间与排名
  - 五指标：success、红蓝战损率、红蓝完好率
- CLI 新增 `brigsim sensitivity`（-t 轨迹数、--reps-per-point、--seed、
  --out 写 sensitivity_morris.csv）；测试 test_m3_sensitivity.py 2 项
- `apply_point`：pydantic model_copy 后按平台类型缩放参数，
  pk/detect 截断 [0,1]，原想定不被修改

### 首版结果（60 设计点 × 10 复制，共 600 次推演）
- 任务成功率敏感度：**speed_scale μ*=0.405 居首** > detect 0.255 >
  pk/visibility 0.210 > mtbf 0.195——冲击任务中机动速度最关键，
  符合教条直觉；探测次之，毁伤/能见度再次
- 红战损：speed 0.270、pk 0.214；蓝战损：speed 0.285、visibility 0.189
- 产物 outputs/sensitivity_morris.csv

### 开源选型记录
- 直接落地 M0 选型 **SALib**（MIT）：morris_sample/morris_analyze，
  未自研抽样与统计；μ* 并列时 rank(method=min) 名次允许并列

### 下一步
- M3 续：营/旅编组、多阶段任务、真实地形 GeoTIFF、参数标定；
  Morris 筛查后上 Sobol 定量方差分解；备件供应链扩展
- M4：Warp GPU 批量后端（列存状态布局已预留）

## 2026-10-13 | trae + 用户 | M3 营级编组（ORBAT 模板）+ 多阶段进攻任务

### 完成内容
- 成建制模板（`data/orbat.py` + schema `UnitTemplate`）：连级模板按队形
  自动展开成单位，营级想定不再手摆几十个点
  - 三种队形：line 横队 / column 纵队 / wedge 前三角（1 尖兵 + 成对展翼）
  - anchor 为队形几何中心（展开后质心落在锚点），heading 0=北顺时针；
    自动生成 unit_id（前缀+序号）与中文编制标签
  - Scenario 校验期 model_validator 展开并入 forces，展开后清空模板；
    id 冲突/未定义平台类型直接报错；model_copy 不重验，灵敏度路径无二次展开
- 多阶段任务（schema `MissionPhase` + `Mission.phase_list`）：
  - 红方任一存活单位进占当前目标半径即顺序切换阶段，末段达成=任务成功
  - MesaKernel 增 `_update_phases`/`finished`：末段达成或红方被全歼提前收束
    （不必跑满 duration）；SimState 增全局 phase 标量，snapshot 带 phase 列
  - 单目标想定零改动兼容（phases=None 退化为单阶段）；ToyNumpyKernel 不受影响
- 营级想定 `scenarios/example_battalion.yaml`：红重型合成1营
  （2 坦克连 wedge + 2 装步连 line + 火力连反坦克排 = 44 台）三阶段进攻
  蓝加强防御连（前沿 9 ATGM + 12 坦克 + 6 步战 = 27 台）
- 可视化：逐阶段目标信标（中间阶段琥珀色、末段绿色+阶段名标签）、
  阶段切换 TextLog 播报（WARN/CRITICAL 分级）；既有单位/射界/烟柱零改动

### 验证
- pytest 17 项全绿（新增 8 项：队形几何/质心、id 冲突、未知类型、
  model_copy 不重复展开、单阶段兼容、营级展开数量、三阶段推进提前成功、
  被全歼提前失败）；ruff 全绿
- 营级 40 复制（71 单位、≤160 tick）：success 0.95±0.07、红战损 0.35±0.06、
  蓝战损 0.98；约 0.8s/复制（i7 单核级，M4 Warp 批量化的动机数据点）
- 默认种子回放：阶段切换 tick 7（进攻出发阵地）→ 26（突破前沿）→
  39（夺占纵深要点，成功）；m3_battalion.rrd 截图确认三阶段信标与播报
- 首轮 44v18 想定 success=1.0 无区分度，蓝方加强到 27 台后分布拉开；
  参数平衡仍属粗调，待 M3 标定

### 素材渠道（本轮追加，仍免登录直链）
- Kenney Tower Defense Kit 为中世纪题材无坦克；Babylon CDN 无 Tank 模型；
  GitHub 仅见 0 星休闲游戏 glb 仓库（TankarioGlb 等），风格化质量不优于
  现有 CC0 Tank2。写实 MBT 维持原结论：待用户本机专有素材或 Sketchfab token

### 下一步
- M3 续：真实地形 GeoTIFF、参数标定、Sobol 定量分解、旅级想定、备件供应链
- M4：Warp GPU 批量后端（71 单位 × 百复制的 Python 循环已显慢）

## 2026-10-13 | trae + 用户 | M3 GeoTIFF 真实地形接入（合成峡谷 DEM 先行）

### 完成内容
- 地形双来源（`kernel/terrain.py`，HeightField 接口不变）：
  - Terrain 增 `source`（synthetic/geotiff）+ `geotiff_path`
  - `HeightField.from_geotiff`：rasterio 读单波段 DEM，GDAL 双线性
    `read(out_shape=...)` 直接重采样到 grid_n（不引 scipy），NaN 填水域；
    粗版像元阵列线性拉伸到 size_m、暂不用 CRS/transform——真实测绘数据
    预先裁剪成方形战区即可同名替换，后续再补坐标变换与通行性栅格
  - Provider 把相对 GeoTIFF 路径解析为相对想定 YAML 目录的绝对路径
- 合成峡谷 DEM `scenarios/terrain/dem_demo.tif`（385² float32，EPSG:32650，
  生成脚本 `outputs/make_dem.py`，可复现）：
  - 东西向摆动走廊（谷底 ~25m）+ 南北两侧平滑抬升山地（至 ~390m，山顶积雪带）
  - 一道蛇形南北向河障在 x≈5200 横切进攻轴线（水面 2m < water_level 5，
    机动系数在水面×0.4），含河岸阶地；FBM 破碎起伏
- example_battalion 切换到 GeoTIFF 地形（想定零改动其余部分）；
  可视化零改动即显示雪山-走廊-河障（地形网格/水面/配色全部沿用 M2 管线）

### 开源选型记录
- 直接用 **rasterio**（BSD，manylinux/win wheel 自带 GDAL，无需手装 GDAL）
  做 DEM 读取与重采样，未自研任何栅格 IO；高程生成沿用 opensimplex（已提为
  核心依赖：内核地形模块本就 import，CI 此前只装 dev extras 实际跑不起来，
  本次把 opensimplex/rasterio 入核心依赖，CI 改为 `.[dev,combat,analysis]`）

### 验证
- pytest 20 项全绿（新增 3：DEM 重采样/插值趋势、缺 path 报错、
  营级想定相对路径解析+机动系数区间）；ruff 全绿
- 营级 40 复制（DEM）：success 1.00、红战损 0.39±0.05、蓝战损 0.98
  （vs 程序地形 0.95/0.35，差异在小样本噪声内；河障未显著迟滞——
  宽 110m 浅水面按现有粗机动模型一个 tick 即涉过，渡场/桥梁/泥泞通行性
  留待后续通行性栅格细化）
- 默认种子回放：阶段 6→22→32 tick；m3_battalion_dem.rrd 截图确认
  雪山夹峙走廊、河障峡谷、林地基底与阶段信标
- 顺带记录：CI 依赖修复（M1 起 combat/analysis extras 漏装）

### 下一步
- M3 续：参数标定、Sobol 定量分解、旅级想定、通行性栅格（渡场/桥梁）
- 真实测绘 DEM 到位后同名替换 dem_demo.tif 即可
- M4：Warp GPU 批量后端

