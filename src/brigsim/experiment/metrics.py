"""三大指标的口径定义（M0 粗版）。

- 装备战损率 attrition：被击毁单位数 / 参战单位数
- 任务完成率 success：成功复制数 / 总复制数（跨多次运行统计）
- 装备完好率 readiness：（存活且未受损单位数）/ 参战单位数
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..kernel.state import SimState


def per_run_metrics(state: SimState, success: bool) -> dict:
    red = state.side == 0
    blue = state.side == 1

    def attr(mask: np.ndarray) -> float:
        return float((~state.alive[mask]).sum() / mask.sum()) if mask.any() else 0.0

    def ready(mask: np.ndarray) -> float:
        ok = state.alive[mask] & (~state.damaged[mask])
        return float(ok.sum() / mask.sum()) if mask.any() else 0.0

    return {
        "success": bool(success),
        "attrition_red": attr(red),
        "attrition_blue": attr(blue),
        "readiness_red": ready(red),
        "readiness_blue": ready(blue),
    }


def summarize(rows: list[dict]) -> pd.DataFrame:
    """跨复制汇总：均值、标准差、95% 正态置信区间半宽。"""
    df = pd.DataFrame(rows)
    n = len(df)
    out = {}
    z = 1.96
    for col in df.columns:
        mean, sd = df[col].mean(), df[col].std(ddof=1) if n > 1 else 0.0
        out[col] = {"mean": mean, "std": sd, "ci95": z * sd / np.sqrt(n) if n > 1 else 0.0}
    return pd.DataFrame(out).T
