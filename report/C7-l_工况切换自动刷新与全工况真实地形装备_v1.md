# C7-l 工况切换自动刷新 + 八工况差异化真实地形/真实装备核查修复

日期：2026-11-20　服务：`http://127.0.0.1:8848/`　提交：本轮

## 1. 用户反馈与结论

反馈：
1. 八种工况在 3D 里看起来还是同一张地图；不应要求用户 Ctrl+F5，下拉切换就应自动换地图并刷新所有文字/数字/图表/曲线；
2. 检查部分工况装备是否仍以点/球替代。

核查结论（有硬证据）：

| 现象 | 根因 | 处理 |
|---|---|---|
| 八工况同一张地图 | C7-i 把**所有**工况的可见地形全局替换为同一个太行山 DEM 子区（`_visual_terrain()` 无工况参数），所以即使下拉换了 rrd 文件，画面仍一样 | 改为 8 个工况各取真实 DEM 的不同子区/朝向（385² 原图上 272² 窗口，四角+四中点 + 0/90/180/270° 旋转） |
| 切换后只有 3D 变、数据面板残留 | `applyScenarioData()` 只重渲 `rates` 视图；KPI 顶栏、Pareto/灵敏度/代理面板仍在用基线数据；且 `rates.init()` 没清 live 曲线缓存 | 新增 `DASH.renderAll()` 一次刷全套；`rates.init()` 清 `LIVE_BUF`；补 4 个缺失工况的仿真 JSON |
| 装备像点/球 | Web 回放为控体积走 `use_models=False` → 12×7×5 m 盒体；真实 GLB 仅空中三类；且地面模型即使加载也按米制 6 m 给出，在 10 km 全景下只有几个像素 | 低面数地面 GLB（tank/ifv/atgm_team 由 483KB/3.0MB/1.7MB → 14/11/4 KB）+ 统一放大 24×；Web 回放默认开真实模型 |
| 浏览器偶发 WebGPU 错误页 | iframe URL 让 rerun 自动选后端，本机 `requestDevice` 失败后不会可靠回退 | 全部 iframe URL 显式 `renderer=webgl` |

## 2. 代码改动

### 2.1 真实地形差异化（仅可视化层，不动仿真层）
`src/brigsim/viz/rerun_view.py`
- 新增 `_real_dem_native()`（385² DEM + 通行性只读一次盘）、`_resize_grid()`、`_DEM_VARIANTS` 八工况子区表。
- `_visual_terrain(terrain, scenario_name)` 按工况返回不同真实 DEM 子区；`visualize()`、live 预览、live 逐 tick 三个调用点全部传入工况名。
- **仿真层完全不动**：四率/报告权威数字 0.705 / 38 万 / 74 / 0.165 / 0.458 / 64% / 0.758 / 240 / 159 保持不变；LOS 用同一张可见地形，遮挡判定与画面一致。

### 2.2 切换即全量刷新（前端）
- `js/main.js`：新增 `DASH.renderAll()`，统一重渲 KPI 数字 + 四率卡 + 时序/分布/Pareto/Sobol/代理五个视图；`applyScenarioData()` 读该工况 6 份 JSON（模型层缺则回退基线），停旧播放循环后调 `renderAll()`；`previewLiveScene()` 切工况先把面板归零；新增顶栏 `scenario-context` 与各面板标题的工况标签。
- `js/views/rates.js`：`init()` 清 `LIVE_BUF`，防止 live→replay 后曲线仍画旧推演。
- `js/common.js`：rrd URL 带 `?v=Date.now()`（C7-k 已加）+ 固定 WebGL；iframe 每次切换强制重载。
- `index.html` / `css/dash.css`：新增工况徽标与面板标签；版本号 `main.js?v=c927`、`rates.js?v=c921`、`common.js?v=c916`、`dash.css?v=c921`。

### 2.3 全工况真实装备
- `viz/models.py`：新增 `LITE_DIR`、`USE_LITE_GROUND_MODELS`，`ensure_model()` 对 tank/ifv/atgm_team/recon_veh 优先用 `assets/models/lite/*.glb`；新增 `tools/build_lite_models.py` 可复现生成。
- `viz/rerun_view.py`：`GROUND_MODEL_SCALE=24.0`，`_update_unit_models()` 位姿同时缩放模型与接地偏移。
- `dashboard/build.py` / `cli.py`：Web 回放默认 `use_models=True`，`tint_models=False`（避免逐状态重发 GLB 导致 rrd 膨胀）。
- `server.py`：**live 实时推演也改用真实地面 GLB**——预载静态 Asset3D、逐 tick 调 `_update_unit_models(tint=False)`；`_update_unit_models()` 兼容 live 帧缺少 `damaged` 列（否则每 tick 报 `KeyError: 'damaged'`）。兜底预览同样改用真实模型。
- `tools/rebuild_all_rrds.py`：校验地面/空中模型实体数；预期地面数按 `apply_features(knee)` 后的编成计算（example_brigade/real_terrain_brigade 为 161，原始 YAML 164 中的 3 辆保障车被保障参数裁剪，不是渲染缺失）。

### 2.4 补齐工况数据
新增 `tools/build_scenario_data.py`：复制全局模型层 JSON 到 8 个工况目录；为 `brigade_rolling_fortified`、`example_battalion`、`real_terrain_brigade` 构建本工况 timeseries/distribution；`example_brigade` 直接复制根权威数据；补齐缺失 manifest。

## 3. 验证结果

### 3.1 回归测试
```
PYTHONPATH=src .venv/Scripts/python.exe -m pytest \
  tests/test_c7_k_all_scenarios_visual.py tests/test_c7_j_fans_and_air.py \
  tests/test_c7_h_los_occlusion.py tests/test_c7_g_live_scene_switch.py -q
→ 20 passed
```
新增守卫：8 工况可见地形 8/8 唯一；低面数模型存在且 <100KB；每个 rrd 地面/空中模型实体数与 knee 编成一致。

### 3.2 rrd 重建（全部 8 工况，模型实体校验通过）
```
example_company             5.3MB  地面 12/12   空中 0/0
medium_brigade              6.4MB  地面 23/23   空中 0/0
brigade_canyon_clear        6.6MB  地面 13/13   空中 0/0
brigade_plain_fog           5.6MB  地面 11/11   空中 0/0
brigade_rolling_fortified   7.7MB  地面 19/19   空中 0/0
example_brigade            21.3MB  地面161/161  空中10/10
example_battalion          15.9MB  地面 71/71   空中 0/0
real_terrain_brigade       32.4MB  地面161/161  空中10/10
```
（`rebuild_all_rrds.py` 日志里的首轮 MISMATCH 是校验脚本按原始 YAML 164 计算预期导致，已修正为按 knee 编成 161 计算，复验全 OK。）

### 3.3 浏览器实测（无手动强刷）
`tools/verify_scenario_switch.py brigade_canyon_clear` 结果：

| 观测项 | 切换前 → 切换后 |
|---|---|
| iframe 地址 | `replay_example_brigade.rrd?v=…` → `replay_brigade_canyon_clear.rrd?v=…&renderer=webgl` |
| 顶栏工况徽标 | 重型旅·合成兵种示例 → 重型旅·峡谷晴日攻坚 |
| 数据状态文字 | 对应同步变化 |
| KPI 数字 | 完好率 65.0% → 7.0%，战损率 27.8% → 53.9%，修复率 49.1% → 89.1% |
| 5 个图表 SVG 摘要 | 时序/分布内容哈希变化；Pareto/Sobol/代理为全局模型，数据不变但随工况标签重绘 |
| 3D 画面 | 不同真实山地子区；地面单位为可辨认车辆/坦克形体，不再是盒体/点球 |
| 页面/console 异常命中 | 0（WebGPU 错误页已消除） |

截图：`tmp_rr/switch_before.png`、`tmp_rr/switch_after.png`。

补数据工况复测（切到 `real_terrain_brigade`）：iframe/KPI/图表摘要/所有文字标签同样全部变化，MC 200 复制、气象多云正确带入；页面/console 异常命中 0。

工况数据完整性：`build_scenario_data.py` 重建后 8 个工况均为 **6/6** 份 JSON（timeseries + distribution + sensitivity + pareto + surrogate + manifest），不再有"切过去图表还是基线"的空档。

live 模式补充实测：切工况后 iframe 从 gRPC 切到该工况静态 rrd、KPI 归零、工况徽标更新；启动一次 `example_company` 推演到 tick 120，服务端 0 条 `3D 写入失败`（修复前每 tick 报 `KeyError: 'damaged'`）。

## 4. 使用说明

- 打开 `http://127.0.0.1:8848/index.html`，**首次升级后普通刷新一次**以加载新版本前端（版本参数已变，不需要 Ctrl+F5 清缓存）；此后在顶栏下拉切换工况，3D 地图、KPI、四率卡、五个图表、所有工况文字标签都会自动更新。
- 模型层三张图（Pareto / Sobol / 代理）是**全局模型**，科学含义上跨工况共用，因此数据本身不随工况改变；界面通过面板标题的工况标签和重绘明确标示当前上下文。
- 以后改了可视化代码，先执行 `.venv\Scripts\python.exe tools\rebuild_all_rrds.py`；新增想定再执行 `tools\build_scenario_data.py <name>`。
