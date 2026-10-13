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

M3 扩展：地形高程/通行性栅格（GeoTIFF）、气象时间序列、备件清单、修理力量编配。

## 3. 输出：统一事件日志

仿真只产出两类记录（Parquet），可视化与统计都读它：

- `snapshot`：每个 tick 每个单位的位置、生存/受损状态（供回放与完好率曲线）
- `engagement`：交火事件（射击方、目标、是否击毁，供战损归因）

Schema 在 `brigsim/kernel/events.py`，冻结前为 M0 草稿。

## 4. 参数标定（M3）

`calibration/` 模块（待建）：用演习/试验观测数据对比模型输出残差，更新参数版本，
形成"使用—观测—校正—版本化"闭环。
