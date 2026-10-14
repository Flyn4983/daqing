"""统一事件日志：仿真只产出 snapshot 与 engagement 两类记录。

回放（rerun）、指标统计、外部分析共用这份输出（M0 用 DataFrame，落盘 Parquet）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


class EventLog:
    def __init__(self) -> None:
        self.snapshots: list[dict] = []
        self.engagements: list[dict] = []
        # 每 tick 每方一条保障摘要（启用备件供应链时才有数据）
        self.supply_rows: list[dict] = []

    def snapshot(self, tick: int, state, sustain=None) -> None:
        for i in range(state.n):
            self.snapshots.append(
                {
                    "tick": tick,
                    "phase": int(getattr(state, "phase", 0)),
                    "unit_id": state.unit_ids[i],
                    "side": int(state.side[i]),
                    "x": float(state.x[i]),
                    "y": float(state.y[i]),
                    "heading": float(state.heading[i]),
                    "alive": bool(state.alive[i]),
                    "damaged": bool(state.damaged[i]),
                    "under_repair": bool(state.under_repair[i]),
                }
            )
        if sustain is not None and getattr(sustain, "parts", None) is not None:
            under = np.asarray(state.under_repair).astype(bool)
            side_arr = np.asarray(state.side)
            for side in (0, 1):
                self.supply_rows.append(
                    {
                        "tick": tick,
                        "side": side,
                        "spares": int(sustain.parts[side].level),
                        "in_repair": int((under & (side_arr == side)).sum()),
                    }
                )

    def engagement(self, tick: int, shooter: str, target: str, killed: bool, damage: bool) -> None:
        self.engagements.append(
            {
                "tick": tick,
                "shooter": shooter,
                "target": target,
                "killed": killed,
                "damaged": damage,
            }
        )

    def to_frames(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        return pd.DataFrame(self.snapshots), pd.DataFrame(self.engagements)

    def supply_frame(self) -> pd.DataFrame:
        """每 tick 每方备件库存/在修数；未启用供应链时为空表。"""
        return pd.DataFrame(self.supply_rows, columns=["tick", "side", "spares", "in_repair"])
