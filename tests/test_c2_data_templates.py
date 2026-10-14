"""C2 数据接口交付物校验：想定模板可加载、参数目录结构与数据自洽。"""

from pathlib import Path

import pytest
import yaml

from brigsim.data.provider import YamlFileProvider

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = REPO_ROOT / "templates" / "scenario_template.yaml"
CATALOG = REPO_ROOT / "templates" / "param_catalog.yaml"

NUMERIC_DTYPES = {"float", "int"}


def test_scenario_template_loads():
    """模板本身必须是合法想定（synthetic 地形，无外部文件依赖）。"""
    sc = YamlFileProvider("scenarios").load_scenario(str(TEMPLATE))
    assert sc.name
    assert set(sc.platform_types) == {"tank", "ifv", "atgm_team"}
    assert len(sc.mission.phase_list) == 3
    # 保障表完整（含修理所与备件链）
    assert sc.sustainment.repair_teams["red"] >= 1
    assert sc.sustainment.spare_stock is not None
    assert sc.sustainment.repair_station is not None
    # 模板最小可运行编成已展开
    assert len(sc.forces["red"]) >= 1 and len(sc.forces["blue"]) >= 1


@pytest.fixture(scope="module")
def catalog():
    with CATALOG.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_catalog_structure(catalog):
    assert catalog["meta"]["version"]
    sources = catalog["sources"]
    assert {"S1", "S2", "S8", "S9"} <= set(sources)
    params = catalog["parameters"]
    assert len(params) >= 15
    required = {"key", "unit", "dtype", "prototype", "confidence", "sources", "real_data_slot"}
    for p in params:
        assert required <= set(p), f"参数缺字段: {p.get('key')}"
        assert 0.0 <= p["confidence"] <= 1.0
        for ref in p["sources"]:
            assert ref in sources, f"{p['key']} 引用了未定义来源 {ref}"


def test_catalog_numeric_domains_contain_prototype(catalog):
    """数值参数的原型值必须落在声明 domain 内（C3/C4 参数域自洽）。"""
    for p in catalog["parameters"]:
        if p["dtype"] not in NUMERIC_DTYPES:
            continue
        lo, hi = p["domain"]
        assert lo < hi, f"{p['key']} domain 非法"
        proto = p["prototype"]
        values = proto.values() if isinstance(proto, dict) else [proto]
        for v in values:
            assert lo <= v <= hi, f"{p['key']} 原型值 {v} 超出 domain {p['domain']}"


def test_catalog_real_data_slots_present(catalog):
    """关键留白项必须显式标记 real_data_slot（用户真实数据入口不能丢）。"""
    by_key = {p["key"]: p for p in catalog["parameters"]}
    for key in (
        "platform.pk",
        "platform.mtbf_ticks",
        "platform.mttr_ticks",
        "sustainment.repair_station",
        "sustainment.repair_teams",
        "terrain.dem",
        "forces.orbat",
    ):
        assert by_key[key]["real_data_slot"] is True, f"{key} 应标记为留白项"


def test_catalog_ballistics_reserved(catalog):
    """M5 弹道扩展为预留槽：声明存在但内核未实现。"""
    bal = catalog["future_extensions"]["ballistics"]
    assert bal["status"] == "reserved_not_implemented"
    assert {"muzzle_velocity_mps", "cep_m_at_1000m", "component_kill_table"} <= set(bal["params"])
