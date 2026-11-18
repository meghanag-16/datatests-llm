"""Generate dbt data tests with an LLM, validate them, and save them as a test set.

For each model: schema + sample rows -> prompt -> LLM (with timeout and provider fallback,
responses cached on disk) -> YAML parsed and every test checked -> dbt runs the kept tests
once on a copy of the clean snapshot. Tests that do not compile in dbt are dropped; tests
that fail on clean data are kept but listed, because a later scoring step treats them as
broken.

Outputs:
  dbt_project/test_sets/<name>.yml                generated tests only
  dbt_project/test_sets/manual_plus_<name>.yml    manual baseline + generated (duplicates removed)
  reports/generation/<name>/                      prompts, raw replies, report.json, dbt logs

Run from the data_eng folder:
  uv run python -m llm_tests.generate                      # LLM_PROVIDER with fallback
  uv run python -m llm_tests.generate --provider groq      # one provider only
  uv run python -m llm_tests.generate --trial 2            # a fresh generation (new cache entry)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from common.dbt_runner import CLEAN_DB, DBT_DIR, active_test_set, fresh_copy, run_dbt
from llm_tests import cache
from llm_tests.context import load_contexts
from llm_tests.prompt_builder import SYSTEM_PROMPT, build_prompt
from llm_tests.providers import FallbackLLM, fallback_order
from llm_tests.validate import (Rejected, TestSpec, load_test_set, specs_to_dicts, to_yaml,
                                validate_reply)

ROOT = Path(__file__).resolve().parents[1]
TEST_SETS = DBT_DIR / "test_sets"
DEFAULT_MODELS = ["dim_teams", "fct_matches", "fct_training_dataset"]


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def ask(llm: FallbackLLM | None, providers: list[str], prompt: str, trial: int, use_cache: bool) -> dict:
    key = cache.cache_key(prompt, SYSTEM_PROMPT, providers, trial)
    if use_cache and (hit := cache.get(key)):
        print(f"  cache hit ({hit.get('provider')}/{hit.get('model')})")
        return {**hit, "cached": True, "cache_key": key}
    if llm is None:
        raise RuntimeError("No cached reply and no LLM available.")
    reply = llm.complete(prompt, SYSTEM_PROMPT)
    entry = cache.put(key, reply, llm.last_provider, llm.last_model)
    return {**entry, "cached": False, "cache_key": key}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", type=Path, default=CLEAN_DB, help="database to read schemas and samples from")
    p.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    p.add_argument("--sample-rows", type=int, default=10)
    p.add_argument("--provider", help="use only this provider (no fallback)")
    p.add_argument("--name", help="test set name (default: llm_<provider>)")
    p.add_argument("--trial", type=int, default=0, help="change to force a fresh generation")
    p.add_argument("--no-cache", action="store_true", help="ignore cached replies")
    p.add_argument("--manual", type=Path, default=TEST_SETS / "manual_baseline.yml",
                   help="manual test set to merge with")
    p.add_argument("--skip-dbt", action="store_true", help="do not run the generated tests in dbt")
    args = p.parse_args()

    if not args.db.exists():
        print(f"Missing {args.db}. Build the models and run scripts/snapshot_clean.py first.")
        return 1

    providers = [args.provider] if args.provider else fallback_order()
    try:
        llm = FallbackLLM(providers)
    except RuntimeError as exc:
        print(f"Note: {exc} Only cached replies can be used.")
        llm = None

    contexts = load_contexts(args.db, args.models, args.sample_rows)
    con = duckdb.connect(str(args.db), read_only=True)

    kept: list[TestSpec] = []
    rejected: list[Rejected] = []
    per_model: dict[str, dict] = {}
    replies: dict[str, dict] = {}
    try:
        for model in args.models:
            print(f"\n[{model}] {contexts[model].row_count} rows, {len(contexts[model].columns)} columns")
            prompt = build_prompt(contexts[model], contexts)
            try:
                reply = ask(llm, providers, prompt, args.trial, not args.no_cache)
            except Exception as exc:  # noqa: BLE001  (all providers failed, no cache)
                print(f"  FAILED: {exc}")
                per_model[model] = {"error": str(exc)}
                continue
            replies[model] = {"prompt": prompt, **reply}
            try:
                k, r = validate_reply(reply["response"], model, contexts, con)
            except ValueError as exc:
                print(f"  reply rejected: {exc}")
                per_model[model] = {"error": str(exc), "provider": reply.get("provider")}
                continue
            kept += k
            rejected += r
            per_model[model] = {"provider": reply.get("provider"), "model": reply.get("model"),
                                "cached": reply["cached"], "kept": len(k), "rejected": len(r)}
            print(f"  {len(k)} tests kept, {len(r)} rejected")
            for item in r:
                print(f"    rejected ({item.column or 'model'}): {item.reason}")
    finally:
        con.close()

    if not kept:
        print("\nNo valid tests were generated.")
        return 1

    used = sorted({v["provider"] for v in replies.values() if v.get("provider")})
    name = slug(args.name) if args.name else "llm_" + (used[0] if len(used) == 1 else "mixed")
    if args.trial and not args.name:
        name += f"_trial{args.trial}"
    out_dir = ROOT / "reports" / "generation" / name
    (out_dir / "replies").mkdir(parents=True, exist_ok=True)
    for model, rep in replies.items():
        (out_dir / "replies" / f"{model}.prompt.txt").write_text(rep["prompt"], encoding="utf-8")
        (out_dir / "replies" / f"{model}.reply.txt").write_text(rep["response"], encoding="utf-8")

    meta = {"generator": name}
    header = (f"# LLM-generated dbt tests ({name}).\n# Written by llm_tests/generate.py; "
              f"providers: {', '.join(used) or 'cache'}. Do not edit by hand.")
    gen_path = TEST_SETS / f"{name}.yml"

    # Run the tests once in dbt on a copy of the clean data.
    dbt_report = {}
    if not args.skip_dbt:
        gen_path.write_text(to_yaml(kept, args.models, header, meta), encoding="utf-8")
        print(f"\nRunning the {len(kept)} kept tests in dbt on a copy of the clean snapshot ...")
        db_copy = fresh_copy(args.db, f"generation_check_{name}")
        with active_test_set(gen_path):
            run = run_dbt("test", db_copy, f"generation_check_{name}", out_dir, tuple(args.models))
        status = {r.test_id: r for r in run.results if r.test_id}
        errored = [s for s in kept if s.test_id in status and status[s.test_id].status == "error"]
        missing = [s for s in kept if s.test_id not in status]
        failing = [s for s in kept if s.test_id in status and status[s.test_id].status == "fail"]
        for s in errored + missing:
            reason = (f"dbt error: {status[s.test_id].message}" if s.test_id in status
                      else "dbt did not run this test")
            rejected.append(Rejected(s.model, s.column, s.signature(), reason))
        drop = {s.test_id for s in errored + missing}
        kept = [s for s in kept if s.test_id not in drop]
        dbt_report = {"log": str(run.log_file.relative_to(ROOT)), "errored": len(errored),
                      "not_run": len(missing), "fail_on_clean_data": [s.test_id for s in failing]}
        print(f"  dbt: {len(kept) - len(failing)} pass, {len(failing)} fail on clean data, "
              f"{len(errored)} error (dropped), {len(missing)} not run (dropped)")

    gen_path.write_text(to_yaml(kept, args.models, header, meta), encoding="utf-8")

    # Merge with the manual baseline; an LLM test identical to a manual one is a duplicate.
    merged_path, duplicates = None, []
    if args.manual and args.manual.exists():
        manual = load_test_set(args.manual, contexts, source="manual")
        manual_ids = {s.test_id for s in manual}
        duplicates = [s.test_id for s in kept if s.test_id in manual_ids]
        merged = manual + [s for s in kept if s.test_id not in manual_ids]
        merged_path = TEST_SETS / f"manual_plus_{name}.yml"
        merged_header = (f"# Manual baseline ({args.manual.name}) plus LLM-generated tests ({name}).\n"
                         f"# Written by llm_tests/generate.py. Do not edit by hand.")
        merged_path.write_text(to_yaml(merged, args.models, merged_header), encoding="utf-8")

    report = {
        "name": name,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "database": str(args.db),
        "models": per_model,
        "providers_used": used,
        "trial": args.trial,
        "counts": {
            "kept": len(kept),
            "rejected": len(rejected),
            "kept_by_type": dict(Counter(s.type for s in kept)),
            "duplicates_of_manual": len(duplicates),
        },
        "dbt_check": dbt_report,
        "kept_tests": specs_to_dicts(kept),
        "rejected_tests": [r.__dict__ for r in rejected],
        "duplicates_of_manual": duplicates,
        "files": {"generated": str(gen_path.relative_to(ROOT)),
                  "merged": str(merged_path.relative_to(ROOT)) if merged_path else None},
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    print(f"\nKept {len(kept)} tests ({dict(Counter(s.type for s in kept))}); rejected {len(rejected)}.")
    if duplicates:
        print(f"{len(duplicates)} generated tests are identical to manual baseline tests.")
    print(f"Saved {gen_path.relative_to(ROOT)}")
    if merged_path:
        print(f"Saved {merged_path.relative_to(ROOT)}")
    print(f"Report: {(out_dir / 'report.json').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
