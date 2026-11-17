"""Swappable LLM provider layer with timeouts, short retries and automatic fallback.

Providers (all expose complete(prompt, system=None) -> str):
  gemini         Google Gemini via the google-genai SDK
  groq           Groq (OpenAI-compatible, fixed base URL)
  openrouter     OpenRouter (OpenAI-compatible, fixed base URL, many free models)
  openai_compat  Any other OpenAI-compatible endpoint: NVIDIA NIM, Ollama, ...

Use get_llm() in project code. It tries LLM_PROVIDER first, then the rest of
LLM_FALLBACK_ORDER (only providers that have a key in .env). Each request has a
hard timeout and at most LLM_MAX_RETRIES quick retries before moving on, so a
call can never hang or retry forever. The provider and model that answered are
kept in .last_provider and .last_model so results can record them.
"""
from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _env_float(var: str, default: float) -> float:
    return float(os.getenv(var, "").strip() or default)


def _env_int(var: str, default: int) -> int:
    return int(os.getenv(var, "").strip() or default)


def timeout_seconds() -> float:
    return _env_float("LLM_TIMEOUT_SECONDS", 30)


def log(message: str) -> None:
    print(message, flush=True)


RETRY_STATUS_CODES = {429, 500, 502, 503, 504}
RETRY_ERROR_NAMES = {
    "APIConnectionError", "APITimeoutError", "ConnectError", "ConnectTimeout",
    "ReadTimeout", "TimeoutException", "TimeoutError",
}


def _is_retryable(exc: Exception) -> bool:
    """True for temporary failures: rate limits, overloaded servers, timeouts, network blips."""
    # Gemini errors carry an int .code; OpenAI-style errors carry an int .status_code
    # (their .code is a text label), so check both.
    for attr in ("status_code", "code"):
        code = getattr(exc, attr, None)
        if isinstance(code, int) and code in RETRY_STATUS_CODES:
            return True
    return type(exc).__name__ in RETRY_ERROR_NAMES


def short_error(exc: Exception) -> str:
    text = str(exc).replace("\n", " ")
    return f"{type(exc).__name__}: {text[:160]}{'...' if len(text) > 160 else ''}"


def with_retries(call, label: str = "", max_retries: int | None = None, base_delay: float | None = None):
    """Run call(); on a temporary failure retry a few times with a short backoff, then raise.

    Defaults come from .env: LLM_MAX_RETRIES (1) and LLM_RETRY_BASE_SECONDS (2).
    """
    max_retries = _env_int("LLM_MAX_RETRIES", 1) if max_retries is None else max_retries
    base_delay = _env_float("LLM_RETRY_BASE_SECONDS", 2) if base_delay is None else base_delay
    for attempt in range(max_retries + 1):
        try:
            return call()
        except Exception as exc:  # noqa: BLE001
            if attempt >= max_retries or not _is_retryable(exc):
                raise
            delay = min(10.0, base_delay * 2**attempt) * random.uniform(0.8, 1.2)
            log(f"  {label} temporary error ({short_error(exc)}); retry {attempt + 1}/{max_retries} in {delay:.0f}s")
            time.sleep(delay)


class LLMProvider:
    name: str = "base"
    model: str = ""

    def _complete_once(self, prompt: str, system: str | None = None) -> str:
        raise NotImplementedError

    def complete(self, prompt: str, system: str | None = None) -> str:
        """Send one prompt to this provider only, with timeout and short retries."""
        return with_retries(lambda: self._complete_once(prompt, system), label=self.name)


@dataclass
class GeminiProvider(LLMProvider):
    api_key: str
    model: str
    temperature: float = 0.0
    name: str = "gemini"

    def __post_init__(self) -> None:
        from google import genai
        from google.genai import types

        # Hard timeout (the SDK default is no timeout) and no hidden SDK retries:
        # retries are handled by with_retries so they stay short and visible.
        self._client = genai.Client(
            api_key=self.api_key,
            http_options=types.HttpOptions(
                timeout=int(timeout_seconds() * 1000),  # milliseconds
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )

    def _complete_once(self, prompt: str, system: str | None = None) -> str:
        from google.genai import types

        config = types.GenerateContentConfig(
            temperature=self.temperature,
            system_instruction=system,
        )
        response = self._client.models.generate_content(
            model=self.model, contents=prompt, config=config
        )
        return response.text or ""


@dataclass
class OpenAICompatProvider(LLMProvider):
    base_url: str
    api_key: str
    model: str
    temperature: float = 0.0
    name: str = "openai_compat"

    def __post_init__(self) -> None:
        from openai import OpenAI

        # The SDK default is a 600 s timeout with 2 hidden retries; replace both.
        self._client = OpenAI(
            base_url=self.base_url, api_key=self.api_key,
            timeout=timeout_seconds(), max_retries=0,
        )

    def _complete_once(self, prompt: str, system: str | None = None) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        response = self._client.chat.completions.create(
            model=self.model, messages=messages, temperature=self.temperature
        )
        return response.choices[0].message.content or ""


def _get(var: str) -> str:
    value = os.getenv(var, "").strip()
    return "" if value == "paste-your-key-here" else value


def _require(var: str) -> str:
    value = _get(var)
    if not value:
        raise RuntimeError(f"{var} is not set. Add it to your .env file.")
    return value


# Presets for OpenAI-compatible services: provider name -> (base URL, key var, model var)
OPENAI_COMPAT_PRESETS = {
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY", "GROQ_MODEL"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "OPENROUTER_MODEL"),
}

PROVIDERS = ["gemini", *OPENAI_COMPAT_PRESETS, "openai_compat"]


def key_var_for(name: str) -> str:
    """Name of the .env variable holding the API key for a provider."""
    if name == "gemini":
        return "GEMINI_API_KEY"
    if name in OPENAI_COMPAT_PRESETS:
        return OPENAI_COMPAT_PRESETS[name][1]
    return "OPENAI_COMPAT_API_KEY"


def has_key(name: str) -> bool:
    return bool(_get(key_var_for(name)))


def get_provider(name: str | None = None, model: str | None = None) -> LLMProvider:
    """Build one specific provider (no fallback). `model` overrides the .env model."""
    name = (name or os.getenv("LLM_PROVIDER", "gemini")).strip().lower()
    if name == "gemini":
        return GeminiProvider(
            api_key=_require("GEMINI_API_KEY"), model=model or _require("GEMINI_MODEL")
        )
    if name in OPENAI_COMPAT_PRESETS:
        base_url, key_var, model_var = OPENAI_COMPAT_PRESETS[name]
        provider = OpenAICompatProvider(
            base_url=base_url, api_key=_require(key_var), model=model or _require(model_var)
        )
        provider.name = name
        return provider
    if name == "openai_compat":
        return OpenAICompatProvider(
            base_url=_require("OPENAI_COMPAT_BASE_URL"),
            api_key=_require("OPENAI_COMPAT_API_KEY"),
            model=model or _require("OPENAI_COMPAT_MODEL"),
        )
    raise ValueError(f"Unknown LLM_PROVIDER '{name}'. Use one of: {', '.join(PROVIDERS)}.")


def fallback_order() -> list[str]:
    """LLM_PROVIDER first, then LLM_FALLBACK_ORDER, keeping only providers with a key."""
    primary = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
    rest = [p.strip().lower() for p in os.getenv("LLM_FALLBACK_ORDER", "gemini,groq,openrouter").split(",")]
    order = []
    for name in [primary, *rest]:
        if name and name not in order and name in PROVIDERS and has_key(name):
            order.append(name)
    return order


class AllProvidersFailed(RuntimeError):
    pass


class FallbackLLM:
    """Tries each provider in order until one answers."""

    def __init__(self, order: list[str] | None = None) -> None:
        self.order = order if order is not None else fallback_order()
        if not self.order:
            raise RuntimeError("No LLM provider has an API key in .env.")
        self.last_provider: str | None = None
        self.last_model: str | None = None

    def complete(self, prompt: str, system: str | None = None) -> str:
        errors = []
        for name in self.order:
            try:
                provider = get_provider(name)
            except Exception as exc:  # noqa: BLE001  (e.g. key set but model missing)
                log(f"Skipping {name}: {short_error(exc)}")
                errors.append(f"{name}: {short_error(exc)}")
                continue
            log(f"Trying {name} ({provider.model}), timeout {timeout_seconds():.0f}s ...")
            try:
                reply = provider.complete(prompt, system)
            except Exception as exc:  # noqa: BLE001
                log(f"  {name} failed: {short_error(exc)}")
                errors.append(f"{name}: {short_error(exc)}")
                continue
            self.last_provider, self.last_model = name, provider.model
            log(f"  Answered by {name} ({provider.model})")
            return reply
        raise AllProvidersFailed("All providers failed:\n  " + "\n  ".join(errors))


def get_llm() -> FallbackLLM:
    """The LLM to use in project code: primary provider with automatic fallback."""
    return FallbackLLM()


def list_models(name: str | None = None) -> list[str]:
    """Model IDs the provider currently offers (needs only the API key, not a model)."""
    name = (name or os.getenv("LLM_PROVIDER", "gemini")).strip().lower()
    if name == "gemini":
        from google import genai
        from google.genai import types

        client = genai.Client(
            api_key=_require("GEMINI_API_KEY"),
            http_options=types.HttpOptions(timeout=int(timeout_seconds() * 1000)),
        )
        return sorted(m.name.removeprefix("models/") for m in client.models.list())
    from openai import OpenAI

    if name in OPENAI_COMPAT_PRESETS:
        base_url, key_var, _ = OPENAI_COMPAT_PRESETS[name]
    elif name == "openai_compat":
        base_url, key_var = _require("OPENAI_COMPAT_BASE_URL"), "OPENAI_COMPAT_API_KEY"
    else:
        raise ValueError(f"Unknown provider '{name}'. Use one of: {', '.join(PROVIDERS)}.")
    client = OpenAI(base_url=base_url, api_key=_require(key_var), timeout=timeout_seconds(), max_retries=0)
    return sorted(m.id for m in client.models.list())
