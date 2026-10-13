"""DataProvider：仿真读取数据资产的唯一入口。

一期实现 YAML 文件；后续新增 DbDataProvider / ApiDataProvider 时，模型层代码不变。
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import yaml

from .schema import Scenario


class DataProvider(Protocol):
    def load_scenario(self, name: str) -> Scenario: ...


class YamlFileProvider:
    """从目录按名称加载想定 YAML：<base_dir>/<name>.yaml。"""

    def __init__(self, base_dir: str | Path = "scenarios") -> None:
        self.base_dir = Path(base_dir)

    def load_scenario(self, name: str) -> Scenario:
        path = Path(name)
        if not path.suffix:
            path = self.base_dir / f"{name}.yaml"
        with path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        sc = Scenario.model_validate(raw)
        # GeoTIFF / 通行性栅格的相对路径以想定 YAML 所在目录为基准
        for attr in ("geotiff_path", "passability_path"):
            rel = getattr(sc.terrain, attr)
            if rel and not Path(rel).is_absolute():
                setattr(sc.terrain, attr, str((path.parent / rel).resolve()))
        return sc
