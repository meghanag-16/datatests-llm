from scoring.holdout import evaluate_split, leave_one_out
from scoring.prune import TestInfo, classify, coverage, greedy_cover

TESTS = [
    TestInfo("manual.a", "manual_baseline", 0),
    TestInfo("llm.b", "llm", 1),
    TestInfo("llm.c", "llm", 1),
    TestInfo("llm.d", "llm", 1),
    TestInfo("llm.e", "llm", 1, broken=True),
]
CATCHES = {
    "manual.a": {"m1", "m2"},
    "llm.b": {"m1"},            # subset of manual.a -> redundant
    "llm.c": {"m3", "m4"},
    "llm.d": set(),             # catches nothing -> low_value
    "llm.e": {"m1", "m2", "m3", "m4", "m5"},  # broken: ignored even though it "catches" all
}
ALL = {"m1", "m2", "m3", "m4", "m5"}
OPS = {"m1": "nulls", "m2": "nulls", "m3": "dates", "m4": "dates", "m5": "ranges"}


def test_classify():
    labels = classify(TESTS, CATCHES, ALL)
    assert labels == {"manual.a": "useful", "llm.b": "redundant", "llm.c": "useful",
                      "llm.d": "low_value", "llm.e": "broken"}


def test_equal_catch_sets_keep_preferred():
    tests = [TestInfo("manual.x", "manual", 0), TestInfo("llm.x", "llm", 1)]
    labels = classify(tests, {"manual.x": {"m1"}, "llm.x": {"m1"}}, {"m1"})
    assert labels == {"manual.x": "useful", "llm.x": "redundant"}


def test_greedy_cover_is_minimal_here():
    kept = greedy_cover(TESTS, CATCHES, ALL)
    assert sorted(kept) == ["llm.c", "manual.a"]
    assert coverage(kept, CATCHES, ALL) == {"m1", "m2", "m3", "m4"}


def test_heldout_split_uses_only_selection_mutants():
    res = evaluate_split(TESTS, CATCHES, OPS, {"nulls"}, {"dates"})
    assert res["kept_test_ids"] == ["manual.a"]
    assert res["heldout_caught_by_kept_set"] == 0
    assert res["heldout_caught_by_full_set"] == 2
    assert res["kept_pct_of_full_on_heldout"] == 0.0


def test_leave_one_out_rotates_every_operator():
    folds = leave_one_out(TESTS, CATCHES, OPS)
    assert [f["heldout_operators"] for f in folds] == [["dates"], ["nulls"], ["ranges"]]
