"""Kernel 抽象接口：仿真内核可替换（M0 numpy 粗内核 -> M1 Mesa/SimPy -> M4 Warp）。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..data.schema import Scenario
from .events import EventLog
from .state import SimState


class Kernel(ABC):
    """一次想定一次运行对应一个 kernel 实例。"""

    name: str = "base"

    def __init__(self, scenario: Scenario, seed: int = 0) -> None:
        self.scenario = scenario
        self.rng = np_rng(seed)
        self.state = SimState(scenario)
        self.log = EventLog()
        self.log.snapshot(0, self.state)

    @abstractmethod
    def step(self) -> None:
        """推进一个 tick；子类实现机动/侦察/交火/保障。"""

    @property
    def finished(self) -> bool:
        return self.state.tick >= self.scenario.mission.duration_ticks


def np_rng(seed: int):
    import numpy as np

    return np.random.default_rng(seed)
