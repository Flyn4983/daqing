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
        # C7-a0：空中架次时序（uav/巡飞弹/直升机）与战场效果点
        # （炮弹出膛/弹着/拦截/烟幕/破障/失控坠机），供回放与实时推送
        self.air_rows: list[dict] = []
        self.effect_rows: list[dict] = []

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

    def engagement(
        self,
        tick: int,
        shooter: str,
        target: str,
        killed: bool,
        damage: bool,
        weapon: str = "direct",
    ) -> None:
        self.engagements.append(
            {
                "tick": tick,
                "shooter": shooter,
                "target": target,
                "killed": killed,
                "damaged": damage,
                "weapon": weapon,
            }
        )

    def air_row(self, tick: int, asset) -> None:
        """空中架次时序点（asset 为 systems.AirAsset）。"""
        self.air_rows.append(
            {
                "tick": tick,
                "asset_id": asset.aid,
                "side": int(asset.side),
                "kind": asset.kind,
                "x": float(asset.x),
                "y": float(asset.y),
                "alt": float(asset.alt),
                "state": asset.state,
            }
        )

    def effect(
        self,
        tick: int,
        kind: str,
        side: int,
        x: float,
        y: float,
        detail: str = "",
    ) -> None:
        """战场效果点：fire_launch/fire_impact/intercept/air_kill/smoke/breach/..."""
        self.effect_rows.append(
            {
                "tick": tick,
                "kind": kind,
                "side": int(side),
                "x": float(x),
                "y": float(y),
                "detail": detail,
            }
        )

    def to_frames(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        return pd.DataFrame(self.snapshots), pd.DataFrame(self.engagements)

    def supply_frame(self) -> pd.DataFrame:
        """每 tick 每方备件库存/在修数；未启用供应链时为空表。"""
        return pd.DataFrame(self.supply_rows, columns=["tick", "side", "spares", "in_repair"])

    def air_frame(self) -> pd.DataFrame:
        """空中架次时序；无空中架次时为空表。"""
        return pd.DataFrame(
            self.air_rows,
            columns=["tick", "asset_id", "side", "kind", "x", "y", "alt", "state"],
        )

    def effect_frame(self) -> pd.DataFrame:
        """战场效果点；无效果时为空表。"""
        return pd.DataFrame(
            self.effect_rows, columns=["tick", "kind", "side", "x", "y", "detail"]
        )
