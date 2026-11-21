import json
from pathlib import Path


def test_dashboard_snapshot_has_what_the_dashboard_reads():
    data = json.loads((Path(__file__).resolve().parents[1] / "dashboard" / "results.json").read_text(encoding="utf-8"))
    assert data["reproduction"]["rows"] and data["scoring"] and data["stability"]
    for row in data["scoring"]:
        assert row["minimal_set"] <= row["working_tests"]
        assert row["heldout_fixed"]["caught_by_kept_set"] <= row["heldout_fixed"]["caught_by_full_set"]
