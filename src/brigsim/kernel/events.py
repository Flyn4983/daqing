"""统一事件日志：仿真只产出 snapshot 与 engagement 两类记录。

回放（rerun）、指标统计、外部分析共用这份输出（M0 用 DataFrame，落盘 Parquet）。
"""

from __future__ import annotations

import pandas as pd


class EventLog:
    def __init__(self) -> None:
        self.snapshots: list[dict] = []
        self.engagements: list[dict] = []

    def snapshot(self, tick: int, state) -> None:
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
