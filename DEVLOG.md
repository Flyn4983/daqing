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

## 2026-10-13 | trae + 用户 | M3 通行性栅格：道路/桥梁/无桥河面

### 完成内容
- `HeightField` 增第二栅格 `passability`（(n,n)，默认全 1）：
  - `Terrain.passability_path`（想定字段，相对路径同 geotiff_path 解析）
  - `from_geotiff` 同法 GDAL 双线性重采样第二栅格并裁剪到 [0,1]，NaN 视为 1
  - 抽出公共 `_bilinear` 插值（高程/通行性共用）
  - `move_factor` 末尾乘通行性栅格：道路/硬地 1.0、一般地表 <1、
    无桥河面 0.12（与坡度、涉水区系数连乘），结果裁剪到 [0.05, 1]
- 合成通行性 `scenarios/terrain/dem_pass.tif`（与 dem_demo.tif 配准，
  `outputs/make_dem.py` 同脚本生成）：走廊 0.9、山地 0.6、河谷公路 1.0、
  蛇曲河面 0.12、三座桥（y≈3300/5000/6700，与想定主进攻轴线对齐）1.0
- example_battalion 接入 passability_path，想定其余零改动

### 验证
- pytest 23 项全绿（新增 3：栅格加载后道路/桥梁/河面/野地的机动系数分层、
  无栅格默认全 1、营级想定通行性路径解析；注意 GDAL 降采样核会吃邻域像元，
  桥位断言留余量）；ruff 全绿
- 营级 40 复制：success 0.65±0.15（vs 无通行性 1.00/0.95）、
  红战损 0.69、蓝战损 0.88、红完好率 0.29——河障从"一 tick 涉过"变为
  实际战役约束，指标区分度拉开（系数为粗调经验值，待 M3 参数标定）
- 默认种子单次：tick 7 进占阶段一目标后被迟滞于 x≈5300 河线，
  仅 20/44 台过河；河线剖面通行系数中位 0.48、最低 0.07

### 下一步
- M3 续：参数标定（含通行系数经验式）、Sobol、旅级想定、备件供应链
- 可视化：通行性栅格叠加层（当前回放只显示水面，未画道路/桥位）
- M4：Warp GPU 批量后端

## 2026-10-13 | trae + 用户 | M3 Sobol 定量方差分解

### 完成内容
- `experiment/sensitivity.py` 增 `run_sobol`：SALib Sobol 采样器
  （Saltelli 方案、不算二阶，设计点 N×(D+2)）+ `sobol.analyze`，
  输出每个指标/参数的 S1（一阶主效应）、ST（含交互总效应）及 95%
  自助置信半宽，按 ST 排序；抽出 `_evaluate_points` 与 Morris 共用
  "设计点 × 公共随机数复制"评估回路
- CLI `brigsim sensitivity --method morris|sobol --sobol-n N --reps-per-point R`
  （默认仍为 Morris），Sobol 结果另存 sensitivity_sobol.csv
- 测试：新增 Sobol 冒烟（结构/有限性/排序，n_base=4），共 24 项全绿；ruff 全绿

### 验证（连级想定，112 设计点 × 4 复制）
- visibility 与 speed_scale 主导 success/战损/完好率，且 ST≫S1
  （如 success：visibility S1=0.87/ST=1.22，speed S1=0.19/ST=1.08）
  → 主要通过参数间交互起作用；pk_scale 一阶主效应明显；
  detect_scale 总效应接近 0（当前粗侦察模型下分辨力弱，待标定）
- ST 之和 >1 且置信区间偏宽：交互占主导 + N=16 偏小，
  后续营级/旅级跑批取 N≥64 并对照 Morris 排序

### 素材调研
- Poly Pizza（免登录直链）坦克均为低多边形风格化资源，无写实 MBT；
  open3dmodel 有写实 M1A2（多格式含 .dae）/M2A3（.3ds），
  许可为 CC BY-NC（原型设计阶段可用），但下载端点 Cloudflare 挑战 +
  广告重定向，自动化浏览器无法完成下载；写实装备素材仍待
  用户登录 Sketchfab 或提供专有素材本地路径

### 下一步
- M3 续：旅级想定上 N≥64 Sobol、参数标定、备件供应链
- 可视化：通行性栅格叠加层
- M4：Warp GPU 批量后端

## 2026-10-13 | trae + 用户 | M3 备件供应链：库存约束 + 周期补给

### 完成内容
- `SustainmentSpec` 增 `spare_stock`（各方初始库存，缺省 None=无限备件，
  旧想定行为不变）与 `resupply_interval_ticks/resupply_qty`（周期投送）
- `sustainment/repair.py`：
  - 启用库存时每方一个 simpy.Container，并起周期补给进程
  - 维修流程改为 送修延迟 -> 备件请领 -> 抢修组排队 -> MTTR -> 归建；
    等件在占用抢修组之前（不窝工修理力量）；缺货请领挂起直至补给
  - 排队/等件期间被战毁则返还已请领备件，标志位正确清理
- example_battalion 启用：红 8 / 蓝 5 初始基数，每 30 tick 补 4 件
- 测试 `test_m3_spare_parts.py`：1 件库存 3 台待修时后两台挂起、
  t=15 补给解束且库存账目正确；None 时无限备件旧行为不变；
  全套 26 项全绿，ruff 全绿

### 验证（营级 40 复制）
- success 0.70±0.14、红完好率 0.32、蓝完好率 0.09
  （vs 无库存约束 0.65/0.29/0.11，差值在 95%CI 噪声内）
- 默认种子单次：红方 5 tick 零库存、蓝方也见底后随补给恢复——
  约束确实绑定但当前补给基数偏宽松，参数待标定（不做无依据调参）

### 下一步
- M3 续：旅级想定、N≥64 Sobol、参数标定（含备件基数/补给周期）
- 可视化：通行性栅格叠加层、备件库存时间曲线
- M4：Warp GPU 批量后端

## 2026-10-13 | trae + 用户 | M3 旅级想定（104v63，规模与性能基线）

### 完成内容
- 新增 `scenarios/example_brigade.yaml`：
  - 红方重型合成旅 104 台：合成1营（44，一梯队）+ 合成2营（44，
    后方 500m 跟进二梯队）+ 旅属北翼反坦克队 6 / 南翼侦察装步连 10
  - 蓝方营级防御体系 63 台：一线 27（同营级想定）+ 纵深阵地
    反坦克 18、坦克 12、翼侧步战 6
  - 三阶段进攻（出发阵地 → 强渡峡谷突破前沿 → 夺占纵深要点 x=9000），
    220 tick；旅级保障：抢修组 6/4、备件 16/10、每 30 tick 补 8
  - 复用 GeoTIFF 双栅格地形与通行性，内核/可视化零改动
- 测试 `test_m3_brigade.py`：展开计数 104v63、三阶段、
  通行性网格、整局可跑完且指标有限；全套 27 项全绿，ruff 全绿

### 验证
- 初版蓝方 51 台时 success=1.000（红方 104 台数量优势平推，无区分度）；
  按营级想定同样的粗调办法给纵深既设阵地补 4 个反坦克组（51→63），
  20 复制：success 0.70±0.21、红战损 0.72、蓝战损 0.94、红完好率 0.25
- 性能基线（M4 动机）：155→167 单位规模下单复制约 1.4–1.6 s；
  N=64 Sobol（448 点 ×4 复制）估算约 50 分钟，
  百复制批量分析已显慢，Warp 数据并行后端收益明确

### 下一步
- M3 续：参数标定、旅级 N≥64 Sobol（可夜间批量跑）
- 可视化：备件库存曲线
- M4：Warp GPU 批量后端（numpy 列存状态已就绪）

## 2026-10-13 | trae + 用户 | M3 可视化：通行性栅格叠加层（道路/桥梁）

### 完成内容
- `viz/rerun_view.py` 新增 `_log_passability()` 并在 `visualize()`
  静态要素阶段（地形之后、单位之前）调用：
  - 公路：`p>0.95` 旱地节点 → 土黄半透明贴片（RGBA 205,180,110,210），
    贴地形面 +0.6m，实体路径 `world/passability/roads`
  - 桥梁：水面节点（`h<water_level+1`）中 `p>0.45` → 灰褐贴片
    （140,126,104,235），板面抬到水面以上 +0.8m，
    实体路径 `world/passability/bridges`
  - `_pass_quads()`：栅格节点批量构造四边形贴片（4 顶点/格 +
    两组三角形索引，单次 Mesh3D 调用）
  - `_dilate4()`：4 邻域一像素膨胀，把降采样后断裂的细线连成连续条带
  - 全域 p=1.0（无通行性栅格）时自动跳过，旧想定画面不受影响
- 营级想定录制 `outputs/m3_passability.rrd` 截图验证：
  连续土黄道路条带沿蛇曲河谷延伸，灰褐桥板跨过河面，语义清晰

### 阈值选择依据（踩坑记录）
- 第一版用 `p>0.55`：普通野地本身就是 0.6、走廊 0.9，导致全域刷黄；
  数值核验后道路改用旱地中唯一接近 1 的 `p>0.95`
- 桥梁原始栅格峰 1.0 经 GDAL bilinear 降到 65² 后被河面 0.12 稀释到
  约 0.83（河面中位约 0.28），故桥梁阈值取相对低的 0.45 且限定水面节点
- 降采样使线状地物出现单像素缺口，`_dilate4` 一格膨胀后连续
  （边界用 np.roll 回绕，道路/桥均不在边界，无副作用）
- 全套 27 项测试全绿，ruff 全绿

### 下一步
- M3 续：参数标定、旅级 N≥64 Sobol（夜间批量）
- 可视化：备件库存时间曲线进回放
- M4：Warp GPU 批量后端

## 2026-10-13 | trae + 用户 | M4 切片 1：Warp 向量化交战内核（单环境，CPU/GPU 双后端）

### 开源调研（开源优先原则，两路）
- Mesa 侧：Mesa core 无任何 GPU/JAX/CuPy/Warp 后端；官方扩展
  mesa-frames（Apache-2.0，列式 AgentSet）GPU 仍在 Roadmap——等不来，自建
- Warp 模式借鉴（NVIDIA/warp Apache-2.0，本机 1.12.1）：
  example_dem/sph 的 HashGrid 邻居循环（目标搜索后续用）、
  example_graph_capture 的 ScopedCapture（消除逐 tick launch 开销，批量用）；
  mujoco_warp 的 nworld 批量 SoA（env 维平铺模式）
- 交战语义参考 TABX（MIT，JAX 高吞吐战斗沙盒，FOV/hurtbox 全向量化）、
  SAGESim（MIT，CuPy ABM，薄壳+列存形态最近）；FLAMEGPU2(AGPL)/bvr_sim(GPL)
  仅看架构不拷代码
- 素材线：免登录直链渠道已穷尽——GitHub 全量搜索/Khronos 官方资产/
  three.js/Cesium/PlayCanvas/Wikimedia 均无写实现代 MBT 成品 GLB；
  仅 OGA Stridsvagn103（CC-BY，84MB 7z+Blender 工程，无炮塔 S 坦克）
  与 CC0 Abrams .blend 素模，均需 Blender 转换且非成品。
  写实 M1A2/T-72 维持待用户登录 Sketchfab 或提供本机专有素材

### 完成内容
- `uv pip install warp-lang==1.12.1` 进 .venv（此前仅全局 Python3.9 有）；
  wp.init 识别 cuda:0（GTX 1650 Ti 4GB，sm_75）
- 新增 `combat/warp_kernel.py`（约 470 行）：
  - SimState numpy 列作 host 镜像，真值在 wp.array（f32/i32 SoA），
    每 tick 边界与 host 同步一次
  - `@wp.func` 设备端 `_grid_at/_move_factor`：双线性地形 + 坡度/水面/
    通行性连乘，与 HeightField.move_factor 同式（含 size/n 的口径）
  - `_nearest_tracked`：共享航迹内稠密 O(n²) 最近敌方（n≤200 每 tick 几万对，
    批量 env 维再换 HashGrid）
  - 内核序列：k_cooldown → k_sense（每线程 rand_init 独立流，
    atomic_max 写航迹失效 tick）→ k_clear×4 → k_move（红蓝教条机动）
    → k_engage（outcome 0/1/2 + want_kill/want_dmg）→ k_apply
  - SimPy 保障留 CPU：tick 初 advance 后 H2D damaged/under_repair，
    tick 末 D2H 后把新战损登记 combat_damage；阶段推进/快照/指标沿用 host
  - `WarpKernel(scenario, seed, device=None)`：None 优先 cuda:0 自动回退 cpu
- CLI `run` 增 `--kernel mesa|warp`、`--device cuda:0|cpu`
- 新增 `tests/test_m4_warp_kernel.py`（4 项，CPU 后端跑 CI 安全）：
  指标合法、事件日志结构/outcome 互斥、存活单调+死亡冻结+机动不越界、
  与 Mesa 跨 20 复制分布对拍（战损/完好率均值差 ≤0.12，success ≤0.30）
- 全套 31 项全绿，ruff 全绿

### 已知语义差异（并行 ABM 标准取舍，DEVLOG 留痕）
- 航迹：Mesa 按单位索引顺序 last-writer；Warp atomic_max 取更晚失效
- 交战：Mesa 同 tick 顺序激活，先被杀者不开火；Warp tick 初存活者齐射，
  可能同 tick 互杀（战损/完好率分布对拍差异在噪声内）
- RNG：numpy 全局流 vs warp 每线程流，不同源故只比分布不比轨迹

### 性能基线（旅级 167 单位，含快照/SimPy，warm 后均值）
- Mesa 1.383 s/复制 → Warp CPU 0.081 s/复制（**约 17×**，向量化 +
  去掉 Python agent/字典开销，CPU 后端本身多线程）
- Warp GPU 单环境 0.123 s/复制：launch + tick 边界同步占主导，
  单环境不划算，符合预期——收益在 env 维批量（切片 2）
- 旅级 N=64 Sobol（约 1800 复制）：Mesa ~42 分钟 → Warp CPU 粗估 ~2.5 分钟

### 下一步
- M4 切片 2：env 维批量复制（状态按 env*n+i 平铺，4GB 显存分批）、
  HashGrid 目标搜索、ScopedCapture 整 tick 图录制 replay
- SimPy 保障的批量处理策略（稀疏事件 CPU 编排 or 向量化近似）
- M3 续：参数标定、备件库存曲线进回放

## 2026-10-13 | trae + 用户 | M4 切片 2：env 维批量复制 + 旅级 N=64 Sobol 落地

### 完成内容
- 新增 `combat/warp_batch.py`：`WarpBatchKernel` / `run_batch()`
  - E 个复制的状态按 env*n+i 平铺到同一组 wp.array，一条 launch
    dim=E*n 推进全部环境；active[env] 冻结提前结束的环境
  - 共享航迹 tracks 扩为 (E*2*n)；目标点 oxs/oys 按环境数组下发；
    阶段推进在 host 向量化（每环境红方最近距离 + 阶段切换）
  - SimPy 保障保留 CPU：每环境一个 SustainmentModel，damaged/
    under_repair 是批量 host 大数组的切片视图，整批 H2D/D2H 每 tick 一次；
    新战损用 flatnonzero 稀疏登记，不逐单位扫
  - 播种：wp.rand_init(base_seed+env, tick*n+i)，与单环境公式一致
  - 批量只收最终指标，不出逐射事件日志（回放仍走单环境内核）
- `experiment/runner.py`：run_replications 增 batch_device/batch_size，
  批量路径分批上设备（4GB 显存预算），事件日志由一次额外单环境运行产出
- CLI run/sensitivity 增 `--kernel warp`、`--device`、`--batch`、`--batch-size`
- 新增测试：批量 E=4 与同种子逐个单环境的五项指标**逐位相等**
  （轨迹级硬保证，比分布对拍更强）；全套 32 项全绿，ruff 全绿

### 性能基线（旅级 167 单位，warm 后，含快照/SimPy/host 同步）
- E=448 一批：GPU 4.44s（**9.9 ms/复制**）、CPU 11.58s（25.8 ms/复制）
- 对比 Mesa 1383 ms/复制：批量 GPU 约 **140×**；切片 1 单环境 Warp GPU
  123 ms → 批量 env 维再提约 12×；E=16 首批含编译约 5s
- 显存：448 环境状态数组约 5MB 级，4GB 显存余量充足，HashGrid 后再估
- 旅级 N=64 Sobol（448 点 ×4 复制 = 1792 次）单环境 Warp CPU 数分钟跑完
  （Mesa 原估约 50 分钟），outputs/sensitivity_sobol.csv 已产出

### 旅级 Sobol N=64 结果要点（ST 总效应）
- speed_scale 是全指标首要驱动：success ST≈0.60、红战损 0.77（S1=0.81
  近纯主效应）、蓝战损 0.75、红完好 0.76——机动决定接敌节奏与暴露时间
- mtbf_scale：success ST≈0.56、红完好 0.22（保障可用性经交互放大）
- pk_scale：红战损 ST≈0.30（S1=0.18）；visibility 各指标 0.15–0.19；
  detect_scale 各指标 ST≤0.09（C2 航迹共享下侦察参数不敏感）
- 交互特征：success/mtbf 的 ST 显著大于 S1，强交互；战损类 speed 近线性
- N=64 置信较 N=16 明显收窄；结论用于后续标定的参数优先级排序

### 下一步
- HashGrid 目标搜索替换稠密 O(n²)、ScopedCapture 图录制（再压 launch 开销）
- Sobol 想定参数化（每设计点一批复制：批量内核支持 per-env 参数列）
- M3 续：按灵敏度优先级做参数标定、备件库存曲线进回放

## 2026-10-14 | trae + 用户 | M4 切片 3：per-env 参数批量（设计点×复制整体上 GPU）

### 完成内容
- `WarpBatchKernel` 构造改为 `(scenarios: Scenario | list[Scenario], seeds: list[int])`：
  支持每环境一个独立想定（灵敏度设计点）；单个想定自动复制为 E 份；
  校验各想定编制规模 n 一致（地形共享），否则 ValueError
- 参数列由 tile 共享改为按环境拼接（col() concatenate 各想定属性列）；
  新增每环境能见度数组 a_vis 与每环境种子数组 a_seed0，
  内核内 `wp.rand_init(seed0[env], tick*n+i)`、探测/射程 reach 乘 vis[env]
- 阶段目标点按环境从各自 mission.phase_list 构造（h_ox/h_oy），
  _update_phases 按环境取相位序列
- 保障：每环境一个 SustainmentModel，新战损 flatnonzero 稀疏登记到
  对应环境的模型；批量只收最终指标
- 新对外接口：`run_batch(scenarios, seeds, device=None)` 与
  `run_replications_batch(scenario, base_seed, n_runs, batch_size=128)`
- `experiment/sensitivity.py` 新增 `_evaluate_points_batch`：
  env 顺序点主序 (p, r)，CRN 种子只随复制号 r 变化（公共随机数），
  分批调 run_batch；run_sensitivity/run_sobol 增 batch_device/batch_size
- runner.py / cli.py：run 与 sensitivity 均支持 `--batch --batch-size --device`
- 新增测试：3 个自定义设计点 ×2 复制，批量与逐个单环境五项指标
  **逐位相等**（atol 1e-9）；全套 33 项全绿、ruff 全绿

### 正确性验证
- 全量 448 个 Sobol 设计点 ×4 复制（CPU 后端）：单环境逐个跑 vs
  per-env 批量跑，五项指标 **max abs diff = 0.0，差异 cell 数 0**
- GPU 批量结果与 CPU 路径存在微小数值差异（如 success 的 speed ST
  0.597→0.568、detect ST 0.088→0.284）：排查确认同后端（CPU）逐位
  一致，差异来自 CPU/CUDA 的 f32 规约与数学函数微差，经交战正反馈
  放大为轨迹分歧——确定性、可复现，非逻辑错误；灵敏度结论排序
  （speed/mtbf 主导）不改变

### 性能
- 全批量旅级 N=64 Sobol（1792 次仿真）GPU wall 45.1s，
  其中约 1792 个 SimPy 环境构建占大头，纯 GPU 计算约 18s

### 下一步
- ~~HashGrid 目标搜索替换稠密 O(n²)~~（切片 4 完成）、ScopedCapture 图录制压 launch 开销
- SimPy 环境构建占比上升，后续评估 host 侧构建复用/向量化
- M3 续：按灵敏度优先级（speed/mtbf/pk）做参数标定、备件库存曲线进回放
- 写实装备素材：免登录直链渠道已穷尽（OGA 仅 S 坦克无炮塔 CC-BY 模型、
  CC0 Abrams .blend 素模），待用户登录 Sketchfab 或提供本机专有素材

## 2026-10-14 | trae + 用户 | M4 切片 4：HashGrid 邻域查询替换稠密 O(n²)

### 完成内容
- 复用 NVIDIA Warp 内置 wp.HashGrid（3D 哈希网格，z=0/dim_z=1 当 2D 用；
  官方 example_sph 同款 hash_grid_query 迭代器），不自研空间结构
- 单网格容纳全部环境：各环境坐标沿 x 平移 env*stride（stride=地形宽+4
  单元格），环境间隔大于查询 stencil 外探距离，天然不跨环境；哈希碰撞
  落入的异环境点由 `k/n != env` 过滤 + 距离二次过滤兜底
- 网格每 tick **重建两次**：sense 前（机动前位置）、engage 前
  （单位每 tick 走一个步长，实测不重建会漏掉新进入射程的目标，
  表现为同种子轨迹逐位分歧）
- sense：半径=各自探测 reach 的邻域查询；engage：半径=武器射程
  （全局最近在射程外则不可能开火，与稠密全扫描语义等价）；
  蓝方追击转向保留稠密——C2 共享航迹全方共享，目标出生时只在某友邻
  探测距内，开火单位自身可距目标任意远，半径查询无界（实测出现
  4175m 的在-track 目标），该路径仅蓝方调用、无随机数、O(蓝*n)
- 随机流改造：sense 每候选 (tick,i,j) 独立 rand_init（高位偏移
  SENSE_KEY_OFF=1<<28 错开交战段键空间），判定结果与候选遍历顺序
  无关——单环境稠密（j 升序）与批量网格（任意序）逐位等价；
  单环境 WarpKernel.k_sense 同步改造
- 等距目标消歧：网格版取较小索引，与稠密 j 升序+严格小于一致
- 附带修复：批量 _update_phases 红方最近距离改 float64 计算，与单环境
  host 口径统一，消除成功半径边界 f32/f64 翻转导致的结束 tick 不一致

### 正确性
- 全套 33 测试全绿、ruff 全绿；E=4 批量 vs 逐个单环境五项指标逐位相等、
  per-env 参数批量逐位相等（CPU 后端硬断言 atol 1e-9）
- 排查方法留痕：逐 tick 对比 tracks/alive/damaged/cooldown/位置，
  定位过两个真问题——移动后网格陈旧（二次重建解决）、phase f32/f64
  边界（精度统一解决）

### 性能（旅级 167 单位，E=448，warm 后）
- GPU 4.47s（10.0 ms/复制），与切片 2 稠密版 4.44s 基本持平：当前
  探测/武器量程覆盖地形尺度 20–40%，3×3 stencil 候选数接近全量 n，
  两次基数排序重建的开销抵消了邻域收益
- 价值在复杂度类别：O(n²)→O(n·k)，编制扩大/密度提高（更大想定、
  多营同屏）时差距拉开；当前瓶颈在 launch 开销与 host SimPy 构建，
  即切片 5（ScopedCapture）的目标

### 下一步
- ~~ScopedCapture 图录制压 launch 开销~~（切片 5 完成）
- host 侧 SimPy 构模/向量化（图捕获后成为主要开销）
- M3 续：按灵敏度优先级做参数标定、备件库存曲线进回放
- 写实装备素材：待用户登录 Sketchfab 或提供本机专有素材

## 2026-10-14 | trae + 用户 | M4 切片 5：CUDA 图捕获 + 修复机动内核跨线程竞争

### 完成内容（图捕获）
- 复用 Warp 官方 `wp.ScopedCapture`/`wp.capture_launch`（example_graph_capture
  同款），不自研：每步设备序列 9 次 kernel launch + 2 次 grid.build 录制成
  一个 CUDA graph，tick 2 起每步只 replay 一次
- 关键约束处理：
  - **标量参数会在录制时被固化**——每 tick 变化的 `tick` 改为单元素设备
    数组 `a_tick[0]`，host 每步 assign 当前值，内核按引用读取；k_b_sense/
    k_b_move/k_b_engage 三个内核签名同步改造
  - **grid.build 可捕获性**：官方测试无覆盖，先写探针实测——首次 build 会
    分配 CUB 排序临时缓冲，预热后的 build 可正常进入/重放图，结果正确且
    重放确定；据此编排"首步直接启动（编译内核+预热分配）→同步→录图"
  - host 段（SimPy 保障、H2D/D2H、阶段判定）留在图外；数组构造时一次
    分配、生存期指针不变，满足图重放前提
- `use_graph` 开关（None=CUDA 自动开/CPU 回退）透传到 run_batch，
  便于对拍与无 GPU 环境复现

### 重要 Bug 修复：机动内核跨线程位置读写竞争
- 图/直对拍测试间歇失败（单跑过、全量跑挂），逐 tick 快照取证定位根因：
  k_move/k_b_move 中每个线程一边写自己的 x[g]/y[g]，一边稠密扫描读取
  **其他线程正在写**的目标位置 x[k]——读到本 tick 更新前还是更新后的值
  取决于线程调度。单位接近后（tick≈15）出现 ~0.1m/0.002rad 级轨迹抖动
- 该竞争自切片 2 起就在 GPU 上存在，此前 GPU/CPU 指标分歧被误记为
  "f32 噪声"；修复为机动段双缓冲：move 前 wp.copy 位置快照 xp/yp
  （同一条流有序），目标方位/地形因子/位移全部基于 tick 起点快照，
  写回 x/y；单环境 k_move 与批量 k_b_move 同步改造、语义一致

### 正确性
- 全套 35 测试全绿、ruff 全绿；新增 2 个 GPU 测试（无 CUDA 自动 skip）：
  图重放 vs 直接启动 E=4 五项指标逐位相等、per-env 参数批量逐位相等
- 8 轮重复运行：直接/直接、图/图、图/直接全部逐位一致；单环境 GPU 内核
  6 轮逐位一致；E=448 旅级图/直 448×5 指标 max diff 全 0
- 修复后确定性语义变化使指标均值小幅移动（如 attrition_blue
  0.826→0.843），蒙特卡洛统计现在建立在可复现轨迹之上

### 性能（旅级 167 单位，E=448，warm 后 best of 3）
- 图重放 4.22s（9.4 ms/复制） vs 直接启动 4.82s（10.8 ms/复制），约 12%
- 收益受限于每 tick host 段（448 个 SimPy 环境 advance + 整批 H2D/D2H +
  numpy 阶段判定），launch 开销已不是大头；下一步优化指向 host 侧

### 下一步
- ~~host 侧 SimPy 构模/向量化~~（切片 6 画像后定向调整，见下）
- 更大 batch_size 显存实测
- M3 续：参数标定、备件库存曲线进回放
- 写实装备素材：待用户登录 Sketchfab 或提供本机专有素材
- 网络恢复后补推 c5709aa（切片 4）与本切片提交

## 2026-10-14 | trae + 用户 | M4 切片 6：host 段耗时画像与两处去 Python 化

### 画像结论（cProfile + 分段计时，E=448 旅级 108 tick）
- SimPy 事件步进合计约 67%：sustain advance 44% + 新战损登记 23%；
  阶段判定 11%，D2H 8%，H2D 5%，**GPU 本身仅 8%**（图捕获后）
- 微基准拆解：448 个独立 simpy.Environment 每 tick 重入 env.run
  （共 2 万次调用）约让 SimPy 成本比一次 run 到底翻倍；但合并为单一
  全局环境不可行——全局事件堆更大（O(log n) 插入变差）且冻结环境需
  进程中断语义，风险/收益不成立
- 战损登记 12,794 次首次受损，每次 env.process 向含 ~167 个待办事件
  的堆插入约 0.06ms，是 SimPy 进程式 DES 的结构性开销

### 落地改动
- `_update_phases` 向量化：E×n 红方最近距离一次算完（float64 inf
  掩码替代每环境 Python hypot 循环），阶段跨越多 tick 循环保留
  （阶段数很少）；0.46s→0.35s
- 战损登记去重：用步初 host 口径 (damaged, under_repair) 掩码过滤，
  仅本 tick 首次受损且未在抢修中的单位调 combat_damage（自然故障已
  在 advance 自行入队、重复受损原本就被守卫跳过）；同时去掉每 tick
  第二次 a_dmg D2H。正确性由 35 测试（含 CPU 逐位对拍、GPU 图/直
  对拍）兜底，五项指标 maxdiff 0
- 决策：**不**自研向量化 DES 替代 SimPy——与开源优先原则冲突，
  且当前 9.4ms/复制（万次蒙特卡洛约 94s）满足迭代需要；若未来
  想定规模/批量再上一个量级再单独立项评估

### 验证
- 35 测试全绿、ruff 全绿；E=448 图/直 448×5 指标 maxdiff 0
- wall 改善被机器噪声淹没（best 4.2~4.4s），主要价值在开销定位、
  语义去重与阻止后续在错误方向（合并环境）上投入

### 下一步
- M3 续：按 speed/mtbf/pk 灵敏度优先级做参数标定
- 更大 batch_size 显存实测（4GB 预算边界）
- 写实装备素材：待用户登录 Sketchfab 或提供本机专有素材

## 2026-10-14 | trae + 用户 | M4 切片 7：4GB 显存边界实测 + 默认 batch_size 调整

### 实测方法
- outputs/probe_vram.py：每 E 一个独立进程（避开 Warp mempool 跨档累积），
  旅级 n=167，测构造后 / 首 tick 后（编译+grid.build+图捕获）/ 全程跑完
  三个测点的 GPU 增量显存（nvidia-smi）与进程 RSS（psutil）

### 数据
| E | 并发单位 | GPU 增量峰值 | host RSS | 全程 ms/复制 |
|---|---|---|---|---|
| 448 | 74,816 | 86 MB | 378 MB | 6.4 |
| 1024 | 171,008 | 86 MB | 494 MB | 6.5 |
| 2048 | 342,016 | 118 MB | 693 MB | 7.5 |
| 4096 | 684,032 | 150 MB | 1,108 MB | 8.5 |
| 8192 | 1,368,064 | 172 MB | 1,910 MB | 8.2 |
（注：探测时桌面环境已占 GPU ~3.56GB；各档活跃 tick 数随种子不同，
ms/复制 仅作量级参考）

### 结论
- **4GB 显存对旅级批量根本不构成约束**：列存总量极小（E=8192 时
  137 万单位 × ~25 列 + 39 万网格单元，增量才 172MB），网格 dim_x
  随 E 线性但绝对量可忽略
- 真正开销在 host：RSS ≈ 0.23MB/E（每环境 SimState + SimPy 进程群，
  E×167 个 failure_loop 进程）；每次 WarpBatchKernel 构造有 ~1.5s
  固定成本（地形构建、SimPy 起进程、首批 H2D），分批越细浪费越多
- 默认 batch_size 128→**512**（runner/sensitivity/cli 共 6 处）：
  E=512 档 GPU <100MB、RSS ~400MB，4GB 卡安全；448 次复制由 4 次
  构造降为 1 次

### 验证
- 35 测试全绿、ruff 全绿（默认值变更不影响逐位语义）
- 切片 4/5/6 三个积压提交已在网络恢复后推送（30c4066..479c8a4）

### 下一步
- M3 续：按 speed/mtbf/pk 灵敏度优先级做参数标定、备件库存曲线进回放
- 写实装备素材：待用户登录 Sketchfab 或提供本机专有素材

## 2026-10-14 | trae + 用户 | M3：备件库存/在修时间曲线进回放

### 完成内容
- `EventLog.snapshot` 增加可选 sustain 入参：启用备件供应链时每 tick
  记录每方一行（spares 库存、in_repair 在修数），新增 `supply_frame()`，
  `to_frames()` 二元签名不变（10 处解包零改动）
- Mesa/Warp 两单环境内核步末传入 self.sustain；`visualize` 增 supply
  参数，rerun 蓝图右侧标签页新增"备件库存/在修"时序（红/蓝各两条）
- 营级想定验证（启用备件）：红 8→0（缺货）→周期补给回 5、蓝 5→0→1，
  在修峰值红 5/蓝 4；38 测试全绿、ruff 全绿
- 提交 1e2c59c

## 2026-10-14 | trae + 用户 | M3：旅级想定参数标定首版（目标带 + scipy DE）

### 问题诊断（标定动因）
- 竞争修复后旅级基线 512 复制：success=1.000（天花板），红战损 0.541、
  蓝战损 0.843；结束时刻探针（outputs/probe_finish.py）显示红方
  p50=45 tick（p5-p95=42-49，max 85）即完成三阶段，而 duration=220——
  **时间余量约 4 倍，时间压力完全没绑定**，灵敏度对 success 失效
- 复跑 Sobol（N=64×8，竞争修复后）：speed 全面主导（战损/完好 S1
  0.70-0.75）、mtbf 第二（success ST 0.49）、pk 次之，detect≈0；
  杠杆优先级与既往结论一致

### 开放参考依据（不做无依据调参）
- MBT 最大越野速度：M1A1 48.3 km/h、豹 2 约 55 km/h（搜狐引简氏/
  armyrecognition）；火力下接敌运动显著低于最大值，旋钮定义为
  "接敌运动系数"而非改装备最大速度
- ATGM 单发命中公开值 0.8-0.98（FAS AT-4 Spigot 0.9；Metis
  0.91-0.98），动对动约 0.6；模型 pk 是交战周期等效毁伤，本轮不动基准
- 攻坚经验：防御"3:1 兵力规则"+ Dupuy 攻坚高方差记录；红:蓝 104:63
  ≈1.65:1 进攻既设防御按经验应为苦战——success 目标带取 0.50-0.75
- 目标带全部显式写在 calibration.py BANDS 并注明"原型假设，真实数据
  资产到位后重标定"；蓝战损上界 0.85（阵地被夺时防御方分数损失普遍
  0.8+，战史常见）

### 开源选型
- 复用 scipy.optimize.differential_evolution（scipy 随 SALib 已在环境，
  不引新依赖；考察过 Optuna/MCMC 类方案，对"确定性带匹配+MC 噪声"
  场景 DE 足够）；公共随机数（CRN，128 同批种子跨评估点复用）降噪，
  取整缓存避免重复评估；标定器在 experiment/calibration.py，
  3 个纯函数单测

### 建模补全：方别战斗乘子 side_modifiers
- 两旋钮（接敌系数/持续时间）标定后 success 0.742、红战损 0.607 入带，
  但蓝战损 0.908 居高不下——模型缺防御方不对称要素
- schema 新增 SideModifiers（pk/detect/speed 三方别乘子，缺省 1.0），
  SimState 建列时按 side 乘入（pk/detect clip 到 [0,1]）；
  装备基准参数继续双方共用

### 标定结果（3 旋钮 DE，265 评估点约 5 分钟）
- 最佳邻域：接敌系数≈0.94、duration≈51-52 tick、蓝方 pk 乘子≈1.25-1.30
- 选定 **(0.943, 51, 1.25)**，512 新鲜种子复验：success 0.584±0.043、
  红战损 0.651、蓝战损 0.849，三指标全部入带
- 烘焙进 example_brigade.yaml：tank/ifv/atgm 220/260/120 →
  207/245/113（×0.943，注释保留原值与依据）、duration 220→51
  （≈26 分钟）、side_modifiers.blue.pk_scale=1.25；meta version 0.2/
  confidence 0.4；CLI 另一批种子 512 复测 success 0.551±0.043 同样入带
- 标定后 Sobol：speed S1 升到 0.85-0.93（时间压力绑定后机动速率主导），
  mtbf 对 success 的影响消失（51 tick 内自然故障/修复来不及起作用，
  保障要素在更长时间尺度想定才显影）

### 遗留观察（下一步）
- 现有灵敏度 5 因子都是**双方对称乘子**，pk/detect 同时缩放红蓝会
  互相抵消（ST 一直偏小）；side_modifiers 落地后，灵敏度因子应改为
  方别因子（如 red_pk / blue_pk）才能正确归因
- 备件基数/补给周期尚未标定（M3 供应链条目）
- DE 结果回读有个取整键 bug（res.x 在 duration 边界处与缓存键精度
  不一致），已改为直接重算 point_means

### 验证
- 38 测试全绿（新增 3 标定纯函数测试）、ruff 全绿；YAML 烘焙前后
  GPU 批量指标一致（仅种子段不同的正常波动）

### 下一步
- 灵敏度因子方别化（red/blue 分开）、备件基数标定
- 真实测绘 DEM 同名替换；写实装备素材待用户登录 Sketchfab/提供本机素材

## 2026-10-14 | trae + 用户 | M3：灵敏度因子方别化（8 因子，消除攻防对称抵消）

### 问题
- 标定后 Sobol（5 个对称乘子）：speed 全面主导，pk/detect 的 ST 长期偏小——
  同一乘子同时缩放红蓝，攻方火力增强带来的效应被守方火力增强对冲，
  指标灵敏度无法归因到方别

### 完成内容
- `experiment/sensitivity.py` PROBLEM 5→8 因子：red_pk / blue_pk、
  red_detect / blue_detect、red_speed / blue_speed、mtbf（全局）、visibility
- `apply_point` 不再改平台 pk/detect/speed，改为经 Scenario.side_modifiers
  按方别乘入（复用标定切片机制，Mesa 与 Warp 批量经 SimState 同时生效）；
  因子语义是"相对标定基线的不确定度"，factor=1.0 保持想定原值
  （含已烘焙 blue pk_scale=1.25）；mtbf 仍全局缩放平台参数
- 测试：apply_point 测试改为经 SimState 验证方别列（红 pk×1.5 clip、
  蓝 detect×0.5、蓝 speed×2、mtbf 全局×0.5、原想定不污染）；
  M4 两处 per-env X 矩阵同步改 8 列；38 测试全绿、ruff 全绿

### 方别化 Sobol 结果（N=64，640 点×4 复制，GPU 批量约 3 分钟）
- success：red_speed ST=0.99（S1=0.83）主导；**blue_pk ST=0.16** 升为第二
- attrition_red：**blue_pk S1=+0.31/ST=0.44**（防御火力杀伤攻方，强主效应），
  red_pk ST 仅 0.06——攻防火力对调归因清晰，对称抵消消失
- attrition_blue：red_pk S1=+0.10/ST=0.12（攻方火力杀伤守方），
  blue_pk ST=0.04；readiness 两指标由 blue_pk 与 red_speed 主导
- detect 双方 ST 均 ≤0.02：开阔地形 51 tick 遭遇战中探测不是瓶颈
  （接触后火力效应主导），属真实结论而非抵消；mtbf 仍可忽略
  （短想定故障/修复来不及显影，长想定再评估）
- 存档：outputs/sensitivity_sobol.csv（方别版），
  旧对称版备份 sensitivity_sobol_symmetric.csv

### 下一步
- 备件基数/补给周期标定；增大 Sobol N（现 S1 置信半宽仍偏大）
- 真实测绘 DEM 同名替换；写实装备素材待用户登录 Sketchfab/提供本机素材

## 2026-10-14 | trae + 用户 | M3：在修后送机制 + 备件基数标定（含交战重标定 v2）

### 诊断：备件供应链为何零约束力
- 探针（256 复制）：有限备件 vs 无限备件 readiness 差 <0.01（红 0.316/0.320、
  蓝 0.131/0.131）；红 0.1 tick 缺货、蓝 4.6 tick（51 tick 的 9%）
- 根因不是库存大小：受损单位在修期间（MTTR≈6 tick）滞留原地仍可被瞄准，
  绝大多数在修理窗口内被补刀 killed，存活即基本完好——备件/抢修对终局
  天然不敏感。这是机制缺口，参数标定无法修复

### 机制补全：在修=后送脱离接触（粗版）
- 三内核目标选择统一排除 under_repair 单位（Mesa nearest_tracked、
  Warp _nearest_tracked 两处、WarpBatch _b_nearest_dense/grid）：
  在修单位不参战也不受击，模拟后送至旅修理所；粗版不做物理位移
  （修复后原地归建），物理后送+再前出留后续切片
- Mesa/GPU 时序对齐：战斗受损当 tick 只登记（SustainmentModel.combat_damage
  置 damaged + pending 集合），tick 末 commit_combat_damage 统一入抢修通道，
  与批量内核"设备侧下 tick 才见 under_repair"一致；此前 Mesa 受损当 tick
  立即免疫、Warp 当 tick 仍可被打，分布对拍 attrition_red 差 0.20，修复后
  Mesa-Warp 分布对拍回归阈值内；自然故障路径仍在 tick 初即时入队

### 保障指标进 metrics
- SustainmentModel 计数 req_total/req_immediate/stockout_wait，
  supply_stats() 出 fill_rate（即时满足率，无请领记 1.0）/缺货累计等待；
  per_run_metrics 增可选 supply，runner 与 WarpBatch._collect_metrics 接入

### 交战重标定 v2（机制改变平衡）
- 后送使蓝方受损单位可归建，防御增强；DE 262 评估点（435s）新平衡
  **(z_speed=0.899, duration=57, blue_pk=1.036)**
- 对比 v1 (0.943,51,1.25)：duration 51→57、blue_pk 1.25→1.036——
  归建本身已增强防御，不再需要高方别火力乘子，机理自洽
- 512 新鲜种子复验：success 0.672±0.041、红 0.609、蓝 0.813，全部入带

### 备件网格标定（12 配置×256 复制）
- 目标（显式原型假设）：fill_rate 双方 0.85-0.97（库存管理常用服务水平
  85-95%，进攻战斗允许短时缺货）；交战三指标不出 BANDS；取满足条件的
  最小初始库存（携行基数经济性）
- 唯一可行解：**初始库存红 12/蓝 8（原 16/10）、补给周期 20 tick（原 30）**，
  fillR 0.916/fillB 0.943；"小初始库存+勤前送"优于"大库存+慢补给"
  （16/10@30 时 fillB 仅 0.841 不达标）
- 备件对作战的影响实测可见且温和：fill 从 0.33（8/5@45）到 0.99
  （24/15@20），success 0.60→0.73（约 12pp 跨度）
- 烘焙后 512 全新种子终验（种子 525200 段）：success 0.639、红 0.623、
  蓝 0.822（三指标入带），fill 红 0.903/蓝 0.945，缺货等待红 3.8/蓝 1.6 tick
- YAML meta version 0.3；38 测试全绿（新增 fill_rate/缺货等待断言、
  commit 时序断言）、ruff 全绿

### 遗留（下一步）
- 在修后送尚无物理位移（修复单位原地归建即返火线）；下一切片加
  修理所位置与后送/再前出机动
- 真实保障数据（基数标准/修理周期）替换原型假设；增大 Sobol N
- 真实测绘 DEM 同名替换；写实装备素材待用户登录 Sketchfab/提供本机素材

## 2026-10-14 | trae + 用户 | M3：备件网格标定器固化进 src

### 完成内容
- 新增 `experiment/supply_calibration.py`：把 outputs/calibrate_supply.py 的
  一次性网格标定逻辑提炼为可复用模块——`apply_supply`（库存同比缩放+
  补给周期施加，model_copy 不污染原想定；未启用 spare_stock 直接报错）、
  `point_record`（单点 CRN 均值 + fill/交战双带判定）、`grid_calibrate`
  （z_stock×interval 穷举）、`select_feasible`（可行点按携行经济性排序）
- 目标带显式留痕：FILL_BAND=(0.85,0.97)，交战带复用 calibration.BANDS
- 新增 tests/test_m3_supply_calibration.py 3 项纯函数测试（缩放/周期/
  qty 施加且原想定不变、缺库存报错、可行点筛选与库存和排序）
- 全套 41 测试全绿、ruff lint 全绿（新增两文件按 line-length=100 格式化）

## 2026-10-14 | trae + 用户 | M3：Sobol 增大到 N=128，方别因子稳健性验证

### 运行
- 旅级想定 8 方别因子，N=128（1280 设计点 ×4 复制 = 5120 次推演），
  Warp GPU env 维批量 10 批跑完；产物 outputs/sensitivity_sobol.csv，
  N=64 版备份 sensitivity_sobol_n64.csv 供对照

### 稳健性结论（N=64 → N=128）
- **置信半宽收窄约 1.4–1.6×**（理论 √2≈1.41）：如 success/red_speed
  S1 半宽 0.305→0.195、attrition_red/blue_pk 0.222→0.154
- **五指标 rank 1/2 全部稳定**：
  - success：red_speed ST 0.99→0.97 绝对主导，blue_pk 0.16→0.13 第二
  - attrition_blue：red_speed 0.77→0.65，red_pk 0.12→0.21 第二
  - readiness_red：red_speed 0.79→0.54，blue_pk 0.31→0.27 第二
  - readiness_blue：red_speed 0.82→0.75
  - attrition_red：red_speed 0.74→0.48、blue_pk 0.44→0.41——
    精度提升后两者由"速度一边倒"变为**近乎共同主导**（S1 0.40 vs 0.37，
    CI 相互重叠）：攻方战损由接敌暴露时间与防御火力近乎等权决定，
    N=64 时速度优势被高估，这是本轮最重要的结论修正
- **方别归因符号稳定**：blue_pk→红战损 S1 +0.31→+0.37（防御火力
  杀伤攻方），red_pk→蓝战损 +0.10→+0.13；blue_pk 对蓝方自身战损
  微负且 CI 含 0（防御方火力主要效应在对方伤亡上）
- **detect 双方 ST 两轮均 ≤0.026**：开阔短想定探测非瓶颈属稳健结论；
  **mtbf ST ≤0.09**：57 tick 想定保障要素不显影同样稳健（长想定再评估）
- 中段名次（success 第 4-7、readiness_blue 第 2-4）有互换，
  但互换因子 ST 差小于各自置信半宽，统计上本就并列，不影响决策

### 残余观察（下一步）
- 顶层因子 S1 半宽仍有 0.14–0.23：限制因素已是每点 4 复制的响应噪声
  而非 Sobol 采样量；再压半宽应加 reps（→8）而非继续增大 N



