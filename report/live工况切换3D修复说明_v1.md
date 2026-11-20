# 实时推演工况切换 3D 联动修复说明 v1

> 2026-11-19。问题：实时推演（live）模式下，工况下拉无论选哪个，rerun 3D 界面地形都长得一样。

## 一、现象与定位

用户在 live 模式切换工况，3D 面板只有单位/雾团在动，**地形轮廓始终是同一片 DEM 地貌**（红色扇面覆盖的 example_brigade 基线），canyon（峡谷）、plain（平原浓雾）看不出区别。

三组对照实验（无头 Chromium + 真实后端，切换后充分等待 wasm/rrd 加载，逐像素比对 3D 面板）：

| 实验 | canyon vs fog 差异 | 结论 |
|---|---|---|
| 修复前 live sink preview | 5.7% | 地形没换（只有时序动画在变） |
| replay 静态 rrd 直载 | 34.7% | 地形真切换（沟壑+水域 vs 平坦草原+量测圈） |
| 服务端日志 | `_preview_apply` 已执行、无异常 | 写入发生了，但对 viewer 不可见 |

## 二、根因

rerun 0.39 的 recording 隔离：

1. 服务启动时 `replay_host.py` 用 `rr.log_file_from_path(replay_knee.rrd)` 把基线回放导入 gRPC sink，形成一个**独立 recording**；
2. viewer 连上 gRPC 后显示的是这个导入的 recording；
3. live 工况预览 `_preview_scenario()` 用 SDK `rr.log(..., static=True)` 写的是**当前 recording**（application `brigsim-dashboard`）；
4. 两者不是同一个 recording → preview 写入的地形（`world/terrain`）对 viewer 不可见，`rr.Clear()` 也清不掉导入 recording 的 static 地形 → 画面始终是基线 DEM。

而 replay 模式早在 C7-e 改为**静态 rrd 文件直载**（`/rerun-file/replay_<name>.rrd`），每次切换加载该工况完整 rrd，天然正确——所以 replay 能切、live 不能切。

> 注：7 个想定均已预构建 `outputs/dash/replay_<scenario>.rrd`（6~25MB），地形/气象/编成/作用范围全在里面。

## 三、修复

| 层 | 文件 | 改动 |
|---|---|---|
| 后端 | `server.py` `/api/scene-switch` preview 分支 | 有预构建 rrd 时直接返回 `{ok:true, mode:"static", file:"replay_<name>.rrd"}`；无 rrd 才回退老 sink 预览 |
| 前端 | `main.js` `previewLiveScene()` | `mode=static` 时调 `window.DASH.loadViewerFile(file)` 静态直载；3D 标题/状态条跟随工况；无 rrd 走老路径 |
| 前端 | `common.js` | 新增 `DASH.ensureLiveSink()`：从静态直载态切回 gRPC sink；`loadViewerFile` 置 `__rerun_viewer_static` 标记 |
| 前端 | `main.js` cfg-submit | 启动推演成功后调 `ensureLiveSink()`，确保能看到逐 tick 实时数据 |
| 版本 | `index.html` | common.js / main.js → `?v=c914` |

两条链路：
- **切工况（未推演）**：静态直载该工况 rrd → 地形/气象/编成真实切换；
- **启动推演**：切回 gRPC sink → 红蓝单位逐 tick 实时渲染。

## 四、验证

- 修复后 live canyon vs fog 差异 **5.7% → 34.8%**（与 replay 34.7% 一致），截图确认峡谷沟壑 vs 平坦草原分明；
- `tests/test_c7_g_live_scene_switch.py`：**8/8**（后端返回格式、iframe 静态直载、标记置位、标题/状态跟随、启动推演切回 sink、无 JS 错误）；
- S4 三问题回归 11/11、C7-c 可视化 23/23 全过。

## 五、备注

- live 首次进入仍显示 gRPC sink 的基线 DEM（默认工况提示在下拉框），用户一切工况立即正确切换；如需初始画面也对齐默认工况，可在 boot 后触发一次 `previewLiveScene(默认工况)`。
- 静态 rrd 首次加载约 30~50MB（wasm + rrd），浏览器缓存后切换很快；黑屏自救卡/看门狗沿用既有机制。

> 改动尚未 commit。
