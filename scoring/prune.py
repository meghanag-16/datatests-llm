"""Classify tests by what they catch, and pick a minimal set that catches everything.

Input is a detection matrix: for every test, the set of mutants it catches. All functions
here are pure (no dbt, no files), so they are easy to test and reuse.

Classes (computed over a given set of mutants):
  broken     fails on the clean data, so its failures say nothing about damage
  low_value  catches none of the mutants
  redundant  catches only mutants that another single test also catches
             (its catches are a subset of another test's; ties keep the preferred test)
  useful     everything else
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TestInfo:
    __test__ = False  # not a pytest test class
    test_id: str
    source: str        # the test set it came from, e.g. manual_baseline or llm_gemini
    priority: int      # lower wins ties (earlier test sets first)
    broken: bool = False


def restrict(catches: dict[str, set[str]], mutants: set[str]) -> dict[str, set[str]]:
    return {t: c & mutants for t, c in catches.items()}


def classify(tests: list[TestInfo], catches: dict[str, set[str]], mutants: set[str]) -> dict[str, str]:
    order = sorted(tests, key=lambda t: (t.priority, t.test_id))
    live = [t for t in order if not t.broken]
    caught = restrict(catches, mutants)
    labels = {t.test_id: "broken" for t in order if t.broken}
    for t in live:
        mine = caught.get(t.test_id, set())
        if not mine:
            labels[t.test_id] = "low_value"
            continue
        redundant = False
        for other in live:
            if other.test_id == t.test_id:
                continue
            theirs = caught.get(other.test_id, set())
            if mine < theirs:
                redundant = True
            elif mine == theirs and (other.priority, other.test_id) < (t.priority, t.test_id):
                redundant = True
            if redundant:
                break
        labels[t.test_id] = "redundant" if redundant else "useful"
    return labels


def greedy_cover(tests: list[TestInfo], catches: dict[str, set[str]], mutants: set[str]) -> list[str]:
    """Smallest-ish set of tests that catches every mutant any test catches (greedy set cover)."""
    caught = restrict(catches, mutants)
    candidates = sorted((t for t in tests if not t.broken), key=lambda t: (t.priority, t.test_id))
    uncovered = set().union(*(caught.get(t.test_id, set()) for t in candidates)) if candidates else set()
    chosen: list[str] = []
    while uncovered:
        best = max(candidates, key=lambda t: (len(caught.get(t.test_id, set()) & uncovered),
                                              -t.priority))
        gain = caught.get(best.test_id, set()) & uncovered
        if not gain:
            break
        chosen.append(best.test_id)
        uncovered -= gain
        candidates = [t for t in candidates if t.test_id != best.test_id]
    return chosen


def coverage(test_ids: list[str] | set[str], catches: dict[str, set[str]], mutants: set[str]) -> set[str]:
    out: set[str] = set()
    for t in test_ids:
        out |= catches.get(t, set()) & mutants
    return out
