# 数据接口说明（L0）

目标：**代码与数据分离，数据资产可持续校正、新增、替换来源**，模型代码不随数据变化而修改。

## 1. DataProvider 抽象

```python
class DataProvider(Protocol):
    def load_scenario(self, name: str) -> Scenario: ...
```

- 一期实现：`YamlFileProvider(base_dir)`，从 YAML 文件读取想定；
- 预留实现：`DbDataProvider`（SQLite/PostgreSQL 参数库）、`ApiDataProvider`（数据平台）；
- 想定中每个平台类型参数预留 `source / version / confidence / updated_at` 元数据，支撑参数标定与溯源。

## 2. 参数资产（M0 先有的部分）

- 想定：任务（时长、目标点、成功判据）、地形边界、环境（气象）
- 平台类型：探测距离、武器射程、单发毁伤概率 pk、机动速度
- 兵力编成：红蓝双方单位清单（类型、初始位置）

M3 进展：GeoTIFF 双栅格地形已接入（`terrain.source: geotiff` + `geotiff_path`
+ 可选 `passability_path`）：单波段 DEM 与通行性栅格均经 GDAL 双线性重采样
到 grid_n，像元线性拉伸到 size_m；通行性取值 [0,1]（1=道路/硬地，
0.12=无桥河面，桥=1.0），与坡度/涉水系数连乘进入机动修正；
相对路径以想定 YAML 目录为基准；真实测绘 DEM/通行性数据裁剪成方形战区、
两栅格配准后即可同名替换。
M3 保障：sustainment 支持备件供应链（`spare_stock` 各方初始库存，
缺省 None=无限备件；每次维修消耗 1 件，缺货维修挂起；
`resupply_interval_ticks/resupply_qty` 周期补给投送）。
M3 待办：气象时间序列、分部件备件清单（当前每件维修统一消耗 1）、修理力量编配细化。

## 3. 输出：统一事件日志

仿真只产出两类记录（Parquet），可视化与统计都读它：

- `snapshot`：每个 tick 每个单位的位置、朝向、生存/受损状态、当前任务阶段 phase（供回放与完好率曲线）
- `engagement`：交火事件（射击方、目标、是否击毁，供战损归因）

Schema 在 `brigsim/kernel/events.py`，冻结前为 M0 草稿。

## 4. 参数标定（M3）

`calibration/` 模块（待建）：用演习/试验观测数据对比模型输出残差，更新参数版本，
形成"使用—观测—校正—版本化"闭环。
