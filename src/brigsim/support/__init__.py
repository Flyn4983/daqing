"""作战保障全过程（support flow）轻量扩展层。

设计约束（2026-11-21 用户明确要求）：
- **不改仿真内核、不改整体架构**：本包只读复用 `engine/tools`、事件 parquet、
  代理预测结果；所有新增逻辑都在本包内，通过 server.py 新增少量只读/计算型 API 暴露。
- 输入：图形化录入装备/弹药/油料/保障车辆/其他资源/日常业务要素及数量。
- 输出：多次推演 → 逐件战损清单 → 由清单派生的战损率等指标 → 模板化保障方案（LLM 只写叙述）。
- 部署：Ubuntu ARM / 128GB 统一内存 / Qwen3.8-27B；纯标准库优先，避免重依赖。

模块：
- catalog       标准资源目录（装备/弹药/油料/车辆/器材/日常业务）+ 流程节点定义
- mission_input 输入模型 + 校验 + 落盘
- battle_loss   战损清单生成 + 汇总 + 四率派生与对账
- force_config  对战双方实力配置（红蓝编成/空中架次/方别乘子）注入 Scenario
- plan          保障方案模板槽位填充 + LLM 叙述 + 一致性校验
"""

from .catalog import CATALOG, PROCESS_NODES, SLOT_DICT
from . import force_config

__all__ = ["CATALOG", "PROCESS_NODES", "SLOT_DICT", "force_config"]

# 延迟导入 API 编排层，避免 server.py 导入时的任何循环依赖
def get_api():  # pragma: no cover - 一行转发
    from . import api
    return api
