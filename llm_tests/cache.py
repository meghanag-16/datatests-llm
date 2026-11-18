"""On-disk cache for LLM responses, so a rerun with the same prompt costs no API calls."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / ".llm_cache"


def cache_key(prompt: str, system: str, providers: list[str], trial: int) -> str:
    payload = json.dumps({"prompt": prompt, "system": system, "providers": providers, "trial": trial},
                         sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get(key: str) -> dict | None:
    path = CACHE_DIR / f"{key}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def put(key: str, response: str, provider: str | None, model: str | None) -> dict:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    entry = {"response": response, "provider": provider, "model": model,
             "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (CACHE_DIR / f"{key}.json").write_text(json.dumps(entry, indent=2), encoding="utf-8")
    return entry
