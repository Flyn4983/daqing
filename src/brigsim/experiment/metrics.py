"""指标口径定义（C1 起按 GJB 术语对齐，详见 four_rates 模块）。

- 装备战损率 attrition：累计战损（被击毁）数 / 参战单位数
- 任务完成率 success：成功复制数 / 总复制数（跨多次运行统计，顶层效能指标）
- 装备完好率 readiness：终值完好率 =（存活且未受损且不在修）/ 参战单位数
- 故障率 / 修复率 / 时序平均完好率：传入 sustain（可选 + log）时由
  four_rates_summary 追加（failures_*/repairs_*/repair_rate_*/
  availability_mean_*/failure_rate_*）
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..kernel.state import SimState


def per_run_metrics(
    state: SimState,
    success: bool,
    supply: dict | None = None,
    sustain=None,
    log=None,
) -> dict:
    red = state.side == 0
    blue = state.side == 1

    def attr(mask: np.ndarray) -> float:
        return float((~state.alive[mask]).sum() / mask.sum()) if mask.any() else 0.0

    def ready(mask: np.ndarray) -> float:
        # 完好率（GJB 口径）：可用 = 存活 且 未受损 且 不在修
        ok = state.alive[mask] & (~state.damaged[mask]) & (~state.under_repair[mask])
        return float(ok.sum() / mask.sum()) if mask.any() else 0.0

    out = {
        "success": bool(success),
        "attrition_red": attr(red),
        "attrition_blue": attr(blue),
        "readiness_red": ready(red),
        "readiness_blue": ready(blue),
    }
    if supply:
        out.update(supply)
    if sustain is not None:
        from .four_rates import four_rates_summary

        snap = None
        if log is not None and getattr(log, "snapshots", None):
            snap = log.to_frames()[0]
        out.update(four_rates_summary(state, sustain, snap))
    return out


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
