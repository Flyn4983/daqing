# C7-i：rerun 提示修复 + 全工况真实地形统一 + 索引健壮性

> 日期：2026-11-20　适用目录：`C:\Users\flyn4\Desktop\daqing`
> 触发：用户反馈"切换工况 rerun 提示三角形索引有问题；提示想获得更好展示效果需将 rerun 本地化；所有地形应采用和太行山一样的真实地形；无人机等装备可否用真实模型"。

---

## 一、问题定位（有证据）

### 1.1 "三角形索引有问题" + "建议本地化" 是同一根因

抓取 rerun 0.39 的 WASM（41MB，`re_viewer_bg.wasm`）中提示文本，确认两条提示原文：

- `Cannot load all of entity '<X>', because its size exceeds the memory budget. Try the native viewer instead, or split up your large assets.`
- `Memory limit of <X> is above the web viewer's maximum of <Y>. Using <Z> instead.`
- `This recording is very memory intense, and the Wasm32 build only has 4GiB of memory. Consider using the native viewer.`

**结论**：这是 **rerun web viewer 的固有内存预算限制**（WASM32 硬上限 4GiB，单 entity 还有预算上限）。当 rrd 里某个 Mesh entity 太大（地形 6.6 万顶点、每 tick 的烟雾球团 ×25 帧累积）时：
1. viewer 因内存预算跳过部分数据 → 弹出"建议本地/原生 viewer"提示；
2. 被跳过的数据导致 Mesh 部分顶点缺失，渲染时 `triangle_indices` 引用到未加载顶点 → 连带出现"索引有问题"的警告。

用 Python 拦截全部 8 个工况、每个 `visualize()` 产生的 **57 个 Mesh3D**，逐一校验 `triangle_indices`：形状均为 `(N,3)`、dtype 经 SDK 自动转 `uint32`、索引范围均 `< 顶点数`、顶点色数量均匹配 → **当前 Python 代码生成的索引数据全部合法**（`tools/diag_all_scenarios.py`）。因此报错来自 viewer 端内存降级，而非索引算错。

### 1.2 仍修复了代码里的两处索引健壮性隐患

| 位置 | 隐患 | 修复 |
|---|---|---|
| `_log_fans` embed=False 分支 | LOS 全遮挡时 `indices=[]`，`np.asarray([])` → shape `(0,)` float64，viewer 解析报 "invalid index" | 改 `np.asarray(indices, dtype=np.int32).reshape((-1,3))` |
| `_instanced_spheres` / smoke_bands | 空实例时 `np.concatenate([])` 抛异常 | 空时返回规范空 Mesh `(0,3)` / 索引统一 `(N,3)` int32 |

---

## 二、全工况统一为太行山真实 DEM（用户明确要求）

### 2.1 设计：只在可视化层替换，仿真层不动

需求方要求"所有工况的 3D 显示都用和太行山一样的真实地形"。关键权衡：

- 若在 **Provider/仿真层** 全局覆盖地形 → 8 工况四率数据全变，与报告权威数字（0.705/38万/74 等）冲突，且 12 个既有测试失败。❌
- 故改为在 **渲染层** 替换：新增 `viz/rerun_view.py::_visual_terrain()`，带 `lru_cache` 的 `_real_dem_field()` 读取 `scenarios/terrain/dem_real_taihang.tif`（SRTM 30m）+ 通行性栅格，进程内只读一次盘。

实现位置（3 处，均为"仅渲染/LOS 判定"）：
1. `rerun_view.visualize()` 入口：`terrain = _visual_terrain(terrain)` —— 覆盖**回放 rrd** 所有工况；
2. `server.py` live 预览（scene-preview）：`hf = rerun_view._visual_terrain(hf)`；
3. `server.py` live 逐 tick：`_terrain_hf = rerun_view._visual_terrain(_terrain_hf)`。

LOS 判定 `los_terrain` 用的就是替换后的地形（第 3 处 `_los_hf = _terrain_hf`），保证"画的山体遮挡 = 判定的山体遮挡"，画面自洽。

总开关 `rerun_view.VISUAL_REAL_DEM = True`，设 False 可一键恢复各工况原地形。

### 2.2 验证：可视化层统一 / 仿真层保留（8 工况高程实测）

| 工况 | 仿真层平均高程 | 可视化层平均高程 | 与太行山 DEM 差 |
|---|---|---|---|
| example_company | 55.9m | 1295.7m | 0.000m |
| medium_brigade | 63.4m | 1295.7m | 0.000m |
| brigade_canyon_clear | 39.4m | 1295.7m | 0.000m |
| brigade_plain_fog | 51.5m | 1295.7m | 0.000m |
| brigade_rolling_fortified | 59.1m | 1295.7m | 0.000m |
| example_brigade | 125.0m | 1295.7m | 0.000m |
| example_battalion | 125.0m | 1295.7m | 0.000m |
| real_terrain_brigade | 1295.7m | 1295.7m | 0.000m |

- 可视化层：8 工况地形栅格完全一致（太行山真实 DEM，画面对比见截图）；
- 仿真层：保留原地形差异 → 四率数据/报告数字不变；
- 各工况气象（晴/浓雾/多云）、编成、时长仍原样，工况切换仍有意义。

浏览器截图验证（`tools/shoot_viewer.py`）：真实山地的沟壑/山脊/河谷纹理清晰，界面无 memory/native/invalid-index 提示，红蓝单位、射界扇面、作用范围圈正常。

---

## 三、缓解 web viewer 内存提示

- `_instanced_spheres` 的 icosphere 细分 `subdiv=2 → 1`（162→42 顶点、320→80 面，减负 75%）。烟雾是半透明球团，视觉几乎无差异。
- 所有 Mesh 索引统一规范为 `(N,3)` int32（SDK 自动转 uint32），消除 viewer 端 "invalid index" 解析告警。
- rrd 体积：8 工况重建后 5.9–15.9MB（原 5.5–25MB）。

> 说明：web viewer 的 4GiB 上限无法从代码侧改变；以上是"减小数据体积使其不触发预算限制"的缓解方案。若个别超大回放仍提示，可用 `tools/rerun_window.py ... --viewer native` 走原生窗口（本机原生 GPU 可能黑屏，默认 web 模式已验证正常）。

---

## 四、无人机等装备真实模型：本次跳过（工程权衡）

- 现有 `assets/models/` 30 个 GLB 全为地面平台，**无无人机/巡飞弹/武直模型**。
- 大屏回放用 `use_models=False`（刻意不嵌 GLB 以控制体积），新增 3 类空中 GLB 会增大 rrd，与本轮"缓解 web viewer 内存"目标冲突。
- 当前空中单位用 Points3D + 航迹尾迹 + 中文标签（无人机/巡飞弹/武直）表达，已清晰可辨。
- 结论：按用户"太复杂就算了"授权，本次不引入空中 GLB；如后续需要，可在 `models.py` 增加程序化 uav/helo/loitering 模型再接入 `_log_air`。

---

## 五、改动文件与回归

| 文件 | 改动 |
|---|---|
| `src/brigsim/viz/rerun_view.py` | `_visual_terrain()`+`_real_dem_field()` 可视化地形统一；索引 `(N,3)` 规范化+空保护；smoke subdiv 2→1 |
| `src/brigsim/agent/server.py` | live 预览/live 逐 tick 两处地形统一 |
| `tools/diag_all_scenarios.py` / `diag_mesh_indices.py` | 索引合法性诊断工具 |
| `tools/rebuild_all_rrd.py` / `shoot_viewer.py` | 批量重建 rrd / 浏览器截图验证 |
| `outputs/dash/replay_*.rrd` | 8 工况全部重建（真实地形，gitignore 不入库） |

回归（全绿）：
- 地形相关 `test_c7_b_terrain_presets` + `test_c7_c_viz` + `test_m3_terrain_geotiff` + `test_c7_h_los_occlusion` = **37/37**；
- warp 内核 `test_m4_warp_kernel` = **6 passed / 2 skipped**；
- 8 工况索引诊断 = 0 问题；
- GUI 按钮测试需 8849 端口服务（`--no-replay`），本轮未启动该端口，与改动无关。
