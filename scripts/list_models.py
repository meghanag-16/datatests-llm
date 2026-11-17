"""List the model IDs a provider currently offers, so .env uses a real, current name.

Run from the data_eng folder:
  uv run python scripts/list_models.py gemini
  uv run python scripts/list_models.py groq
  uv run python scripts/list_models.py openrouter          (all models)
  uv run python scripts/list_models.py openrouter free     (only IDs ending in :free)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm_tests.providers import list_models  # noqa: E402

name = sys.argv[1] if len(sys.argv) > 1 else None
only_free = len(sys.argv) > 2 and sys.argv[2].lower() == "free"
models = list_models(name)
if only_free:
    models = [m for m in models if m.endswith(":free")]
print("\n".join(models))
print(f"\n{len(models)} models")
