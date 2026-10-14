"""C5 国产大模型智能体层（模拟本地化部署）。

形态：一个"本地大模型推理服务" + 装备保障智能参谋。
- service.LocalLLMService：本地推理服务的状态/显存/延迟/请求统计（模拟部署形态，
  不加载真实 LLM 权重；7B INT4 量级的典型运维指标）；
- tools.AgentToolkit：大模型"函数调用"工具集，全部由真实数据驱动——
  C3 代理模型秒级四率预测、C4 Pareto 方案表、蒙特卡洛复验结论；
- engine.AgentEngine：规则/模板推理内核（意图识别 → 工具调度 → 中文答复），
  产出对话问答与保障方案一键生成。

设计边界（比赛原型，诚实声明）：智能体的"智能"体现在把仿真/代理/优化结果
按参谋业务语言组织成可执行结论，不声称自然语言理解达到真实 LLM 水平；
接入真实国产大模型时，仅替换 engine 的生成内核，工具集与服务接口不变。
"""

from .engine import AgentEngine, AgentReply
from .service import LocalLLMService
from .tools import AgentToolkit

__all__ = ["AgentEngine", "AgentReply", "AgentToolkit", "LocalLLMService"]
