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
        # C7-b：terrain.preset_family 触发 terrain_presets.preset 工厂，
        # 再用 YAML 里显式给的其他 terrain 字段覆盖（noise_seed/amplitude/
        # water_level/size_m 等都可以在 YAML 里改）
        if sc.terrain.preset_family:
            from .terrain_presets import preset as terrain_preset

            base = terrain_preset(sc.terrain.preset_family)
            # exclude_unset=True：只拿 YAML 里显式写了的 terrain 字段（其余保留 preset 基线）
            override = {k: v for k, v in sc.terrain.model_dump(exclude_unset=True).items()}
            sc.terrain = base.model_copy(update=override, deep=True)
        # GeoTIFF / 通行性栅格的相对路径以想定 YAML 所在目录为基准
        for attr in ("geotiff_path", "passability_path"):
            rel = getattr(sc.terrain, attr)
            if rel and not Path(rel).is_absolute():
                setattr(sc.terrain, attr, str((path.parent / rel).resolve()))
        return sc
