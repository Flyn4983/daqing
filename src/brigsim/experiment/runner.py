"""蒙特卡洛批量运行器：同一想定 N 次独立复制，收集指标与事件日志。"""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd

from ..data.schema import Scenario
from ..kernel.base import Kernel
from .metrics import per_run_metrics


def run_once(
    scenario: Scenario, seed: int, kernel_cls: Callable[..., Kernel]
) -> tuple[dict, Kernel]:
    kernel = kernel_cls(scenario, seed=seed)
    while not kernel.finished:
        kernel.step()
    return per_run_metrics(kernel.state, kernel.mission_success()), kernel


def run_replications(
    scenario: Scenario,
    n_runs: int = 100,
    base_seed: int = 20261013,
    kernel_cls: Callable[..., Kernel] | None = None,
    keep_events_of: int = 0,
) -> tuple[list[dict], tuple[pd.DataFrame, pd.DataFrame] | None]:
    """返回 (每次运行指标列表, 某次运行的事件日志)。

    M4 计划：本函数的循环体替换为 Warp GPU 上的批量复制，接口保持不变。
    """
    if kernel_cls is None:
        from ..combat.toy_kernel import ToyNumpyKernel

        kernel_cls = ToyNumpyKernel

    rows: list[dict] = []
    event_frames = None
    for r in range(n_runs):
        metrics, kernel = run_once(scenario, base_seed + r, kernel_cls)
        rows.append(metrics)
        if r == keep_events_of:
            event_frames = kernel.log.to_frames()
    return rows, event_frames
