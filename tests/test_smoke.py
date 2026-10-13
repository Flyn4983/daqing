"""M0 冒烟测试：端到端跑通，指标口径合法。"""

from brigsim.data.provider import YamlFileProvider
from brigsim.experiment.metrics import summarize
from brigsim.experiment.runner import run_replications


def test_end_to_end():
    sc = YamlFileProvider("scenarios").load_scenario("example_company")
    rows, events = run_replications(sc, n_runs=5)
    assert len(rows) == 5
    for row in rows:
        assert 0.0 <= row["attrition_red"] <= 1.0
        assert 0.0 <= row["readiness_blue"] <= 1.0
        assert row["success"] in (True, False)

    summary = summarize(rows)
    assert set(summary.columns) == {"mean", "std", "ci95"}

    snapshots, engagements = events
    assert not snapshots.empty
    assert {"tick", "unit_id", "x", "y", "alive", "damaged"} <= set(snapshots.columns)
