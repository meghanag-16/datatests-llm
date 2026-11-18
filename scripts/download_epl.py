"""Download English Premier League 2023-24 matches from TheSportsDB.

Same source as the paper's authors (github.com/igargouri10/football-analytics-platform,
scripts/ingest_data.py). They used the old test key "3" with eventsseason.php and got
100 matches. Key "3" is no longer available, and with the current free key "123"
eventsseason.php returns only 15 matches. So by default we request rounds 1 to 10 one by
one (eventsround.php, 10 matches each) and combine them into one file shaped like the
original ({"events": [...]}), which gives 100 matches.

Run from the data_eng folder:
    uv run python scripts/download_epl.py              # rounds 1-10 (default)
    uv run python scripts/download_epl.py --rounds 38  # whole season
    uv run python scripts/download_epl.py --season     # single eventsseason call, like the paper
If data/raw/epl_season_2023-2024.json already exists it is only checked; add --refresh to
download again and overwrite it.
"""
import argparse
import json
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

BASE = "https://www.thesportsdb.com/api/v1/json/123"  # free key "123"; league 4328 = EPL
SEASON_URL = BASE + "/eventsseason.php?id=4328&s=2023-2024"
ROUND_URL = BASE + "/eventsround.php?id=4328&r={r}&s=2023-2024"
OUT = Path("data/raw/epl_season_2023-2024.json")


def fetch_events(url: str) -> list:
    print(f"Downloading {url}")
    with urllib.request.urlopen(url, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8-sig"))
    return (data or {}).get("events") or []


def summarize(events: list) -> None:
    statuses = Counter(e.get("strStatus") for e in events)
    teams = {e.get("strHomeTeam") for e in events} | {e.get("strAwayTeam") for e in events}
    rounds = sorted({int(e["intRound"]) for e in events if str(e.get("intRound") or "").isdigit()})
    print(f"{OUT}: {len(events)} events, {len(teams)} teams, rounds {rounds[:1]}..{rounds[-1:]}")
    print(f"Status counts: {dict(statuses)}")
    print("The paper had 100 finished matches and 20 teams.")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--rounds", type=int, default=10, help="number of rounds to fetch (default 10 = 100 matches)")
    p.add_argument("--season", action="store_true", help="use one eventsseason call instead of rounds")
    p.add_argument("--refresh", action="store_true", help="download again even if the file exists")
    args = p.parse_args()

    if OUT.exists() and not args.refresh:
        print(f"Checking the existing file {OUT} (add --refresh to download again)")
        try:
            events = (json.loads(OUT.read_bytes().decode("utf-8-sig")) or {}).get("events") or []
        except ValueError as exc:
            print(f"FAILED: not valid JSON ({exc})")
            return 1
        if not events:
            print("FAILED: no 'events' in the file")
            return 1
        summarize(events)
        return 0

    events, seen = [], set()
    try:
        urls = [SEASON_URL] if args.season else [ROUND_URL.format(r=r) for r in range(1, args.rounds + 1)]
        for i, url in enumerate(urls):
            got = fetch_events(url)
            if not got:
                print("  no events returned")
            for e in got:
                if e.get("idEvent") not in seen:
                    seen.add(e.get("idEvent"))
                    events.append(e)
            if i < len(urls) - 1:
                time.sleep(2)  # be gentle with the free API
    except Exception as exc:
        print(f"FAILED: {exc}")
        print("Open that URL in a browser to see what the API says.")
        return 1

    if not events:
        print("FAILED: the API returned no events")
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"events": events}, indent=2), encoding="utf-8")
    print(f"Saved {OUT}")
    summarize(events)
    return 0


if __name__ == "__main__":
    sys.exit(main())
