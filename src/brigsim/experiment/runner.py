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
    batch_device: str | None = None,
    batch_size: int = 128,
) -> tuple[list[dict], tuple[pd.DataFrame, pd.DataFrame] | None]:
    """返回 (每次运行指标列表, 某次运行的事件日志)。

    batch_device 非 None 时走 M4 Warp 批量后端（同一想定的复制按 env 维平铺，
    分批上设备）；事件日志由一次额外的单环境 WarpKernel 运行产出。
    """
    if batch_device is not None:
        from ..combat.warp_batch import run_batch
        from ..combat.warp_kernel import WarpKernel

        rows: list[dict] = []
        for start in range(0, n_runs, batch_size):
            count = min(batch_size, n_runs - start)
            rows.extend(
                run_batch(scenario, base_seed + start, count, device=batch_device)
            )
        event_frames = None
        if keep_events_of is not None and 0 <= keep_events_of < n_runs:
            _, kernel = run_once(
                scenario,
                seed=base_seed + keep_events_of,
                kernel_cls=lambda sc, seed=0: WarpKernel(sc, seed, device=batch_device),
            )
            event_frames = kernel.log.to_frames()
        return rows, event_frames

    if kernel_cls is None:
        from ..combat.mesa_kernel import MesaKernel

        kernel_cls = MesaKernel

    rows = []
    event_frames = None
    for r in range(n_runs):
        metrics, kernel = run_once(scenario, base_seed + r, kernel_cls)
        rows.append(metrics)
        if r == keep_events_of:
            event_frames = kernel.log.to_frames()
    return rows, event_frames
