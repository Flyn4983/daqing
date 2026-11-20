# C7-k 全工况地形与装备可视化核查修复（v1）

## 1. 问题现象

用户反馈：八个工况中只有「重型旅 · 合成兵种示例（DEM 地形）」在 3D 回放里显示了要求的真实地形和真实装备，其余工况的地形/装备可视化不符合要求。

## 2. 根因核查（硬证据）

### 2.1 回放 rrd 版本不一致（主因）

`outputs/dash/replay_*.rrd` 的构建时间与最终空中模型代码完成时间不一致：

| 工况 rrd | 构建时间 | 真实空中模型实体数 |
|---|---|---|
| example_company / medium_brigade / brigade_canyon_clear / brigade_plain_fog / brigade_rolling_fortified | 2026-11-20 14:47 | 0 |
| example_battalion | 2026-11-20 14:48 | 0 |
| example_brigade | 2026-11-20 15:04 | 10 |
| real_terrain_brigade | 2026-11-20 15:06 | 10 |

最终空中真实模型代码（C7-j）在 15:07 提交。也就是说，**只有 example_brigade 和 real_terrain_brigade 的 rrd 是在最终代码之后构建的**；其余 6 个是旧文件，自然看不到新模型。

前端工况切换使用静态文件直载：

- `common.js` → `viewerFileTargetUrl()`：`/rerun-file/replay_<scenario>.rrd`
- 文件名固定，**没有缓存破坏参数**，浏览器/代理可能继续返回更早的旧 rrd，进一步放大“只有某个工况正确”的现象。

### 2.2 实时推演逐帧链路从未渲染空中装备

`server.py::_run_worker` 的 `_live_tick()` 原先只写 `live/red`、`live/blue` 红蓝圆点和扇面/作用范围，没有调用 `_log_air()`，所以启动推演后同样看不到真实无人机/武直。

### 2.3 真实地形替换本身没有失效

逐工况直接调用 `rerun_view._visual_terrain()` 验证：8 个工况全部被替换为太行山真实 DEM（高程 884~2025m），与 `example_brigade` 一致。问题出在**旧 rrd 文件 + 缓存**，而非替换逻辑。

### 2.4 地面装备为何仍是轻量盒体（设计取舍，非缺陷）

尝试把地面单位也改为逐车 GLB：

- example_company：5.1MB → 27.3MB；
- example_brigade：12MB → **162~268MB**（每车重复嵌入 GLB，状态染色更会重复整份资产）。

Rerun Web Viewer 运行在 WASM32（约 4GiB 硬上限），这种体积会直接触发“exceeds memory budget / try native viewer”并丢数据。因此 **Web 回放继续保持盒体 + 标签 + 状态色**；真实 GLB 只用于空中三类架次（uav / loitering / helo，3 型共约 14KB），这正是用户此前明确要求的“无人机等用真实装备”。桌面独立回放仍可通过 `--with-models` 使用全地面 GLB。

## 3. 修复内容

1. `tools/rebuild_all_rrds.py`（新增）
   - 统一重建 8 个工况 rrd；
   - 校验每个 rrd 中真实空中模型实体数 == 想定 `air_packages` 数量；
   - 防止再次混入旧文件。
2. `src/brigsim/agent/web/js/common.js`
   - `viewerFileTargetUrl()` 给 `/rerun-file/<file>` 增加 `?v=<timestamp>`，强制每次工况切换拉取最新 rrd。
3. `src/brigsim/agent/server.py`
   - `_run_worker`：预载每个架次的静态 Asset3D；逐 tick 调用 `_log_air()`，实时推演也能看到真实空中装备；
   - `_preview_scenario` 兜底路径同样预载并绘制 tick-0 空中架次。
4. `src/brigsim/viz/rerun_view.py`
   - 抽出 `_preload_air_models()`，回放与 live 共用；
   - 抽出 `_update_unit_models()`（桌面全模型路径复用，Web 回放仍默认盒体）。
5. `src/brigsim/dashboard/build.py`、`src/brigsim/cli.py`
   - `build_replay(use_models=False)` 保持 Web rrd 轻量；新增 `--with-models` 供桌面/调试按需开启全地面 GLB。
6. 前端版本：`common.js?v=c915`、`main.js?v=c924`。

## 4. 重建结果

```text
example_company                  5.1MB  空中模型 0/0   12.1s  OK
medium_brigade                   5.7MB  空中模型 0/0   12.6s  OK
brigade_canyon_clear             6.0MB  空中模型 0/0   13.0s  OK
brigade_plain_fog                5.4MB  空中模型 0/0   11.5s  OK
brigade_rolling_fortified        6.7MB  空中模型 0/0   13.6s  OK
example_brigade                 11.5MB  空中模型 10/10  33.1s  OK
example_battalion               11.0MB  空中模型 0/0   18.1s  OK
real_terrain_brigade            15.3MB  空中模型 10/10  48.2s  OK
全部 8 个工况 rrd 重建并校验通过。
```

只有 `example_brigade` 与 `real_terrain_brigade` 配置了 `air_packages`（各 10 架次），其余工况本来就没有空中单位，因此 0/0 是正确结果。

## 5. 回归验证

- 新增 `tests/test_c7_k_all_scenarios_visual.py`：
  - 8 工况真实 DEM 替换（高程 >500m / >1800m）；
  - 静态空中 Asset3D 预载；
  - rrd 新鲜度守卫（空中模型实体数必须等于 `air_packages` 数量）。
- 全量回归：`test_c7_k + test_c7_j + test_c7_h + test_c7_g` = **18 passed**。
- 浏览器抽查 `example_company`、`example_battalion`、`example_brigade`：
  - 页面报错命中 0；
  - console 关键词命中（triangle indices / memory budget / failed to load mesh）0；
  - 三个工况地形外观一致（均为太行山真实 DEM）。
- 8848 服务重启后 HTTP 200，前端脚本版本已更新为 c915/c924。

## 6. 使用说明

```bat
cd /d C:\Users\flyn4\Desktop\daqing
.venv\Scripts\python.exe -m brigsim.cli agent-serve --host 127.0.0.1 --port 8848
```

浏览器打开 `http://127.0.0.1:8848/index.html`，**Ctrl+F5 强刷一次**（加载新 JS 版本）。之后任意切换工况都会拉取最新 rrd；启动实时推演后空中装备也会以真实模型显示。

以后只要修改了 `rerun_view.py` / `server.py` 的可视化逻辑，先执行：

```bat
.venv\Scripts\python.exe tools\rebuild_all_rrds.py
```
