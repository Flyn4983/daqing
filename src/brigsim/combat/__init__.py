"""交战模型（ABM）。

M0：ToyNumpyKernel —— 向量化粗内核，先打通端到端闭环；
M1：Mesa 多智能体重写（侦察/通信/C2/行为规则）；
M4：同一状态布局移植 NVIDIA Warp GPU kernel 批量运行。
"""
