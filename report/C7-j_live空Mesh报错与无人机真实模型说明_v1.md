# C7-j：live 峡谷场景空 Mesh 报错修复 + 无人机真实 3D 模型

> 日期：2026-11-20　适用目录：`C:\Users\flyn4\Desktop\daqing`
> 触发：用户反馈"实时推演时，重型旅·峡谷晴日攻坚报 `Failed to load mesh '/world/fans': Mesh has no triangle indices`，排查其它场景是否还有此类问题；无人机等能否用真实的装备，而不是圆球替代"。

---

## 一、问题定位：`/world/fans` 空 Mesh 的确切根因

### 1.1 不是"索引算错"，是"遮挡后索引为空仍照常 log"

C7-i 把 `(0,)` float64 空索引规范成 `(0,3)` int32，消除了 viewer 的 "invalid index" 警告。但**本次是另一条报错**：`Mesh has no triangle indices`——viewer 明确拒绝加载"一个连三角形都没有的 Mesh"。

链路：

1. 全工况可视化统一太行山真实 DEM（C7-i，平均高程 1295.7m）后，峡谷等场景四周山体高大；
2. C7-h 的 LOS 遮挡逻辑对扇面逐弧点判通视（`los_terrain.has_los`），峡谷里某单位射界的 **11 个弧点可能全部被山体遮挡** → `vis` 掩码全 False；
3. `_log_fans` 生成三角形索引时要求"弧点 j 与 j+1 均可见"，全遮挡 → `indices=[]`；
4. 修复前代码只看 `fill_v`/`verts` 是否为空（有单位就非空），照常 `rr.log("world/fans", Mesh3D(triangle_indices=(0,3)))`；
5. rerun viewer 拿到一个有顶点、零三角形的 Mesh → 报 `Failed to load mesh '/world/fans': Mesh has no triangle indices`。

### 1.2 修复（两分支都覆盖）

| 分支 | 触发场景 | 修复 |
|---|---|---|
| `_log_fans(outline=True)` | 大屏 live 逐 tick / 嵌入回放（**本次报错路径**） | `indices` 为空时改 `rr.Clear` 而非空 Mesh；亮弧线 `fans_edge` 仍按可见段正常绘制 |
| `_log_fans(outline=False)` | 桌面版 / 独立窗口 | 同样防御 |

`tests/test_c7_j_fans_and_air.py` 用假地形（全遮挡）单测两分支，断言：不再出现任何空 `triangle_indices` Mesh、全部遮挡时 `world/fans` 收到 `Clear`、无遮挡时仍正常出 Mesh。

### 1.3 全场景排查结论

`tools/diag_all_scenarios.py` 扩展到：对全部 8 个工况、用**统一后的真实 DEM** 做 LOS、两个 tick、outline 两分支，逐一校验所有 `visualize()` 产生的 Mesh3D（fans / coverages / smoke / weather / passability / terrain）：

- 新增"空 triangle_indices"检测项（此前只查越界/非三元组）；
- 结果：**8 工况全部 0 问题**。

排查过的所有 Mesh 生成点：
- `_log_terrain` / `_pass_quads`：索引恒非空（有顶点才有 quad）；
- `_log_coverages`：用 `LineStrips3D`，非 Mesh，无此问题；
- `_instanced_spheres` / `smoke_bands`：C7-i 已加空保护，且调用方有 `if centers/if band_buf` 门控；
- `_log_fans`：**唯一**会在真实地形全遮挡时产生空索引的点，已修。

---

## 二、无人机等空中装备改用真实 3D 模型（用户第二次要求）

### 2.1 现状与决策

此前空中单位（无人机/巡飞弹/武直）在回放里用 `Points3D` 圆球 + 文字标签表示，用户明确要求换成真实装备外形。

关键约束：回放 rrd 为控制 web viewer 内存（C7-i）刻意 `use_models=False`（地面单位走轻量盒体）。若为此把全局 `use_models=True`，30+ 地面 GLB 会显著增大 rrd、重新触发内存提示。**决策：空中模型独立于地面开关**——地面继续轻量盒体，**三类空中平台始终用真实 GLB**（3 型共约 14KB，体积可忽略）。

### 2.2 实现

1. `viz/models.py` 复用 trimesh 程序化建模管线（无外部下载、无版权依赖），新增三个 builder 并在 `_BUILDERS` 注册：
   - `uav`：中高空固定翼察打无人机（细长机身 + 大展弦比平直翼 + H 尾翼 + 腹下吊舱），~200 面 / 5KB；
   - `loitering`：巡飞弹（筒身 + 三角翼 + 导引头），~116 面 / 3.3KB；
   - `helo`：武装直升机（串列座舱 + 十字主旋翼 + 尾梁尾桨 + 短翼火箭吊舱），~240 面 / 6KB。
2. `viz/rerun_view.py::_log_air` 增加 `use_models`/`model_logged`/`ground_max` 参数：
   - 航向：air 数据无 heading 列，按轨迹末两点差分 `atan2(dy,dx)` 推算，模型机头 +X 绕 Z 旋转；
   - `Asset3D` 静态资源在**首次 set_time 之前**按 aid 预加载（static 数据绑时序的坑：若在循环内 static log，跳帧后模型消失只剩标签）；最小 rrd 实验确认 Asset3D+Transform3D 机制本身在 web viewer 正常渲染；
   - 米制模型在 10km 视野太小且被密集扇面/标签淹没，`AIR_MODEL_SCALE=50.0` 放大（武直旋翼 ~750m；最小 rrd 实验验证此量级在全景相机下可辨十字旋翼与机身轮廓）；
   - **飞行高度兜底（关键）**：C7-i 统一太行山真实 DEM（最高 ~2024m）后，原仿真想定的飞行高度 alt=900/1500m 低于山峰，飞机被埋进山体（这是"只有标签看不到模型"的真正根因）。渲染时按 `ground_max + 400m` 抬高航迹，保证飞在真实山体之上；
   - 中文型号标签用极小 Points3D 锚点承载（rerun 0.39 无 Text3D）；
   - 被拦截/耗尽/返航时同时 Clear model + label + trail；
   - `AIR_USE_REAL_MODELS=True` 常量可一键退回圆球。
3. 常量 `AIR_USE_REAL_MODELS` 与地面 `use_models` 解耦，App 回放自动启用。

### 2.3 关于 live 模式

排查确认：`agent/server.py` 的 live 逐 tick 路径**原本就不渲染空中单位**（只画红/蓝地面点 + fans/coverages + 气象），所以 live 模式没有"圆球无人机"；本次改动作用于回放 rrd（有空中架次的 `example_brigade` 等）。

---

## 三、验证与交付

- 新增 `tests/test_c7_j_fans_and_air.py`：5/5 通过（全遮挡不发空 Mesh ×2、可见仍出 Mesh、真实模型 Asset3D+Transform3D、轻量回退圆球）；
- 回归：`test_c7_j` + `test_c7_h_los_occlusion` + `test_c7_b_terrain_presets` + `test_c7_c_viz` 共 **36 passed**；
- 全工况空 Mesh 诊断：8/8 OK，总问题数 0；
- 8 个 rrd 用最终代码重建，8848 服务重启；浏览器截图确认峡谷回放无 "no triangle indices" 提示、`example_brigade` 空中单位为真实无人机外形。

### 已知限制

- 空中模型为程序化粗形体（保证可复现、无外部依赖），非实测/扫描网格；如需更高保真，按同名 GLB 替换 `assets/models/{uav,loitering,helo}.glb` 即可（`ensure_model` 对已存在文件直接复用）。
- web viewer 内存预算为 rerun WASM 固有限制（C7-i），本改动未增大 rrd，不会加剧该问题。
