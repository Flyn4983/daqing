# 作用范围地形遮挡（LOS）+ 地形差异化 说明 v1

> 2026-11-20。用户两点反馈：①工况下拉里除太行山外"其余地形好像没变化"；②"装备作用范围若有高山，是不是应该被遮挡才对"。

## 一、核查结论（用数据说话，不靠肉眼）

### 1. 地形重复——用户观察属实，但事出有因

用 `HeightField.from_spec` 加载全部 8 个想定的高程数组，两两计算平均绝对差：

| 想定对 | 地形平均绝对差 | 结论 |
|---|---|---|
| medium_brigade vs brigade_rolling_fortified | **0.0 m** | 完全相同（同 rolling preset + 同 seed） |
| example_brigade vs example_battalion | **0.0 m** | 完全相同（同 `dem_demo.tif` 合成峡谷） |
| canyon / plain / company / 太行山 | 各不相同 | 这几个本来就不同 |

根因：`provider.py:41` 用 `exclude_unset=True` 只取 YAML 显式写的 terrain 字段；两个 rolling 想定都没写 `noise_seed` → 都用 preset 固定 seed（20261016）→ 地形逐像元相同。

### 2. 作用范围无遮挡——真实缺陷

- 内核 `mesa_kernel.py:65,176` **已经**用 `HeightField.has_los()` 判定直瞄射击/雷达通视；
- 但渲染层 `_log_fans`（射界扇面）、`_log_coverages`（作用范围圈）只做"贴地投影"（`height_at` 采样 + 抬升），**完全没调用 LOS**；
- 结果：画面上雷达圈画满全向，内核却因山体遮挡打不到——**画面与行为脱节**。

LOS 遮挡比例实测（半径 1200m，72 方向采样）证明遮挡真实存在且合理：

| 想定 | 中心点 | LOS 可见方向 |
|---|---|---|
| canyon | (5000,5000) | 26/72 = 36% |
| 太行山 | (7000,4000) | 2/72 = 3%（陡峭山地几乎全遮蔽）|
| rolling | (7000,4000) | 17/72 = 24% |

## 二、修复

### 1. 作用范围按内核口径渲染真实作用域（C7-h）

`viz/rerun_view.py`：给 `_log_fans` / `_log_coverages` 增加 `los_terrain` 参数（传**真实未缩放**地形——缩放版 `geo` 的 z_exag 会放大高程导致误判）。

- 射界扇面：逐弧点做 `has_los`，被遮挡方向的三角形**不画**；
- 作用范围圈：逐点判定，连续可见段成条（被遮挡处断开），扫描线终点被遮挡则不打该方向；
- 三条渲染路径全部覆盖：`render()`（replay rrd）、`server.py:424`（live 预览）、`server.py:746`（live 逐 tick）。

裁剪效果实测（单位数相同，对比三角形数）：

| 想定 | 无遮挡 | 加 LOS | 裁剪 |
|---|---|---|---|
| medium_brigade | 50 | 11 | 39 |
| canyon | 50 | 24 | 26 |
| 太行山 | 80 | 40 | 40 |

### 2. 地形差异化（输入条件不变、战果淡化）

**用户决策**：体现地形差异，但输入条件（地形类型）与报告一致，淡化最终战果。

`scenarios/brigade_rolling_fortified.yaml` 加 `noise_seed: 137`（medium_brigade 保持 preset 默认 20261016）：

- 两者仍同为 rolling 丘陵族，起伏 std 17.7 / 21.8m、通行性 mean 均 0.967——与报告"丘陵山脊"输入条件完全一致；
- 地形平均差从 0.0m → **26.9m**，97% 像元不同——视觉上明显区分；
- 报告表11 四想定战果是独立文本（verification.json），不随地形相位自动变化；界面明确标注"战果为原型标定值、非单一地形相位必然结果，以实时推演为准"。

**未改** example_brigade / example_battalion 的 DEM——两者共用 `dem_demo.tif` 是因为都基于"合成峡谷走廊+河障+三桥"的同一战术物理语义，换 DEM 会破坏营级三阶段进攻的地形逻辑；在 UI 说明里注明地形语义同源。

### 3. 修正既有错误

`research.js` verify 页原来把"中型旅快攻"标成 `example_brigade`（错），实际是 `medium_brigade`，已修正；并补充上述口径说明。

## 三、验证

| 项 | 结果 |
|---|---|
| `tests/test_c7_h_los_occlusion.py`（LOS产生遮挡/扇面裁剪/地形差异化） | 3/3 ✅ |
| `tests/test_c7_h_gui.py`（3D 画面 + 像素差） | 6/6 ✅（两丘陵想定像素差 4.6%，confirm 地形已区分）|
| 历史回归：C7-g 8/8、C7-c 23/23、正文表格 23/23、真实地形 14/14 | 全过 ✅ |
| 9 个工况 replay rrd 全量重建（含 LOS + 地形新 seed） | 10:41-10:44 完成 ✅ |
| 核心权威数字 | 全部未变（0.705/38万/74/0.165/0.458/64%/0.758）|

## 四、改动文件

- `src/brigsim/viz/rerun_view.py`：LOS 裁剪逻辑
- `src/brigsim/agent/server.py`：3 处调用传真实地形
- `scenarios/brigade_rolling_fortified.yaml`：地形 seed
- `src/brigsim/agent/web/js/research.js`：口径说明 + 修正错误映射（v=c926）
- `outputs/dash/replay_*.rrd`：9 个全量重建（gitignore，生成产物）
- `tests/test_c7_h_*.py`：新增回归
