"""Setup check: one small call to the LLM layer.

Run from the data_eng folder:
  uv run python scripts/check_llm.py            LLM_PROVIDER first, falls back to the others
  uv run python scripts/check_llm.py groq       test only one provider (no fallback)
  uv run python scripts/check_llm.py all        test every provider that has a key, one by one
Press Ctrl+C at any time to stop.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm_tests.providers import PROVIDERS, FallbackLLM, fallback_order, has_key  # noqa: E402

PROMPT = "Reply with exactly the word: pong"


def show(reply: str) -> None:
    print(f"  Reply: {reply.strip()!r}", flush=True)


arg = sys.argv[1].lower() if len(sys.argv) > 1 else None
try:
    if arg is None:
        print(f"Order: {' -> '.join(fallback_order()) or '(no keys found)'}", flush=True)
        show(FallbackLLM().complete(PROMPT))
    elif arg == "all":
        names = [n for n in PROVIDERS if has_key(n)]
        ok = True
        for name in names:
            try:
                show(FallbackLLM([name]).complete(PROMPT))
            except Exception as exc:  # noqa: BLE001
                ok = False
                print(f"  {exc}", flush=True)
        print("\nAll providers answered." if ok else "\nSome providers failed (see above).", flush=True)
        sys.exit(0 if ok else 1)
    else:
        show(FallbackLLM([arg]).complete(PROMPT))
except KeyboardInterrupt:
    print("\nStopped.", flush=True)
    sys.exit(130)
except Exception as exc:  # noqa: BLE001
    print(f"\n{exc}", flush=True)
    sys.exit(1)
