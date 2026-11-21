"""Held-out evaluation: choose tests using some mutation operators, measure on the others.

Pruning on the same mutants that are later used to judge the result rewards tests for the
exact damage they were selected on. Here the kept set is chosen using only the "selection"
operators and then measured on mutants from operators it never saw.

Two kinds of split are reported:
  fixed         the split named in the config file (selection vs held-out groups)
  leave_one_out each operator in turn is held out and selection uses all the others
"""
from __future__ import annotations

from .prune import TestInfo, coverage, greedy_cover


def evaluate_split(tests: list[TestInfo], catches: dict[str, set[str]], mutant_ops: dict[str, str],
                   selection_ops: set[str], heldout_ops: set[str]) -> dict:
    sel = {m for m, op in mutant_ops.items() if op in selection_ops}
    held = {m for m, op in mutant_ops.items() if op in heldout_ops}
    live = [t.test_id for t in tests if not t.broken]
    kept = greedy_cover(tests, catches, sel)
    full_cov = coverage(live, catches, held)
    kept_cov = coverage(kept, catches, held)
    pct = lambda a, b: round(100 * a / b, 1) if b else None
    return {
        "selection_operators": sorted(selection_ops),
        "heldout_operators": sorted(heldout_ops),
        "selection_mutants": len(sel),
        "heldout_mutants": len(held),
        "kept_tests": len(kept),
        "kept_test_ids": kept,
        "heldout_caught_by_full_set": len(full_cov),
        "heldout_caught_by_kept_set": len(kept_cov),
        "kept_pct_of_heldout": pct(len(kept_cov), len(held)),
        "full_pct_of_heldout": pct(len(full_cov), len(held)),
        # Share of what the full set can catch on unseen damage that the kept set still catches.
        "kept_pct_of_full_on_heldout": pct(len(kept_cov), len(full_cov)),
        "missed_by_kept_but_caught_by_full": sorted(full_cov - kept_cov),
    }


def leave_one_out(tests, catches, mutant_ops: dict[str, str]) -> list[dict]:
    ops = sorted(set(mutant_ops.values()))
    return [evaluate_split(tests, catches, mutant_ops, set(ops) - {op}, {op}) for op in ops]


def loo_mean(folds: list[dict]) -> float | None:
    """Average share of the full set's held-out catches that the kept set keeps, over the
    operators the full set can catch at all (an operator nothing catches says nothing)."""
    scored = [f for f in folds if f["heldout_caught_by_full_set"]]
    return round(sum(f["kept_pct_of_full_on_heldout"] for f in scored) / len(scored), 1) if scored else None
