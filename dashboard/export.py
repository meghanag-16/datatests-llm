"""Collect the numbers the dashboard shows into dashboard/results.json.

reports/ is not committed, so the dashboard reads this small snapshot instead. Rerun after
new experiments:
  uv run python dashboard/export.py
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
OUT = Path(__file__).resolve().parent / "results.json"

LLMS = {"gemini": "Gemini 2.5 Flash", "groq": "gpt-oss-120b"}
DATASETS = {"football": "Football", "olist": "Olist"}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def reproduction() -> dict:
    final = load(REPORTS / "anomaly_results" / "final_summary.json")["summary"]
    rows = [
        {"test_set": "Manual only", "detected": final["manual_only"]["detected"],
         "paper": final["manual_only"]["paper_detected"]},
        {"test_set": "Manual expanded", "detected": final["manual_expanded"]["detected"],
         "paper": final["manual_expanded"]["paper_detected"]},
        {"test_set": "Manual + authors' LLM tests", "detected": final["manual_plus_llm"]["detected"],
         "paper": final["manual_plus_llm"]["paper_detected"]},
    ]
    for key, label in LLMS.items():
        path = REPORTS / "stability" / key / "stability.json"
        if path.exists():
            trials = [t for t in load(path)["trials"] if t["mode"] == "frozen"]
            rows.append({"test_set": f"Manual + {label} tests",
                         "detected": min(t["manual_plus_llm_detected"] for t in trials), "paper": None})
    return {"total": final["manual_only"]["total"], "rows": rows}


def scoring() -> list[dict]:
    out = []
    for ds, ds_label in DATASETS.items():
        base = REPORTS / "scoring" / ("" if ds == "football" else ds)
        for key, label in LLMS.items():
            path = base / f"manual_baseline__llm_{key}" / "summary.json"
            if not path.exists():
                continue
            s = load(path)
            fixed = s["heldout_fixed"]
            out.append({
                "dataset": ds_label, "llm": label,
                "mutants": s["mutants"], "caught": s["mutants_caught"],
                "working_tests": s["candidate_tests"] - s["classes"].get("broken", 0),
                "broken_tests": s["classes"].get("broken", 0),
                "minimal_set": len(s["kept_tests"]),
                "heldout_fixed": {
                    "operators": fixed["heldout_operators"], "mutants": fixed["heldout_mutants"],
                    "kept_tests": fixed["kept_tests"],
                    "caught_by_kept_set": fixed["heldout_caught_by_kept_set"],
                    "caught_by_full_set": fixed["heldout_caught_by_full_set"],
                    "kept_pct_of_full": fixed["kept_pct_of_full_on_heldout"],
                },
                "leave_one_out_mean_pct_of_full": s["heldout_leave_one_out_mean_pct_of_full"],
                "leave_one_out": [{"operator": f["heldout_operators"][0], "mutants": f["heldout_mutants"],
                                   "caught_by_kept_set": f["heldout_caught_by_kept_set"],
                                   "caught_by_full_set": f["heldout_caught_by_full_set"]}
                                  for f in s["heldout_leave_one_out"]],
            })
    return out


def stability() -> list[dict]:
    out = []
    for key, label in LLMS.items():
        path = REPORTS / "stability" / key / "stability.json"
        if not path.exists():
            continue
        for t in load(path)["trials"]:
            if t["mode"] == "fresh":
                out.append({"llm": label, "trial": t["trial"], "tests": t["generated_tests"],
                            "identical_to_trial_1": t["shared_with_trial1"],
                            "detected": t["manual_plus_llm_detected"]})
    return out


def main() -> None:
    needed = [REPORTS / "anomaly_results" / "final_summary.json", REPORTS / "scoring"]
    if not all(p.exists() for p in needed):
        print(f"No experiment results in {REPORTS.relative_to(ROOT)}; keeping the existing {OUT.name}.")
        return
    data = {"exported": date.today().isoformat(), "reproduction": reproduction(),
            "scoring": scoring(), "stability": stability()}
    OUT.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Saved {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
