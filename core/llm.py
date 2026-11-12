"""Free-tier-first text-generation fallbacks behind Gemini.

Gemini remains the primary script model (``core.script_engine``). When Gemini is
not configured, is rate-limited, or is down across every configured model, the
request falls through to whichever of these *optional* providers has
credentials. All of them expose the OpenAI-compatible ``/chat/completions``
contract, so one small client covers them:

=============  ==========================  =============================
Provider       Credential                  Model override
=============  ==========================  =============================
groq           ``GROQ_API_KEY``            ``GROQ_MODEL``
cerebras       ``CEREBRAS_API_KEY``        ``CEREBRAS_MODEL``
openrouter     ``OPENROUTER_API_KEY``      ``OPENROUTER_MODEL``
mistral        ``MISTRAL_API_KEY``         ``MISTRAL_MODEL``
github         ``GITHUB_MODELS_TOKEN``     ``GITHUB_MODELS_MODEL``
ollama         ``OLLAMA_BASE_URL``         ``OLLAMA_MODEL`` (local, free)
custom         ``LLM_FALLBACK_BASE_URL``   ``LLM_FALLBACK_MODEL``
=============  ==========================  =============================

Nothing here is required, nothing is paid by default, and no key is ever logged.
Model IDs change over time: every default can be overridden, and an unknown
model triggers discovery through the provider's ``/models`` endpoint.
"""
from __future__ import annotations

import os
import random
import re
import time

import requests

DEFAULT_ORDER = "groq,cerebras,openrouter,mistral,github,ollama,custom"

_PROVIDERS = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY",
        "model_env": "GROQ_MODEL", "default_model": "llama-3.3-70b-versatile",
    },
    "cerebras": {
        "base_url": "https://api.cerebras.ai/v1", "key_env": "CEREBRAS_API_KEY",
        "model_env": "CEREBRAS_MODEL", "default_model": "llama-3.3-70b",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1", "key_env": "OPENROUTER_API_KEY",
        "model_env": "OPENROUTER_MODEL",
        "default_model": "meta-llama/llama-3.3-70b-instruct:free",
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1", "key_env": "MISTRAL_API_KEY",
        "model_env": "MISTRAL_MODEL", "default_model": "mistral-small-latest",
    },
    "github": {
        "base_url": "https://models.github.ai/inference", "key_env": "GITHUB_MODELS_TOKEN",
        "model_env": "GITHUB_MODELS_MODEL", "default_model": "openai/gpt-4o-mini",
    },
    "ollama": {
        "base_url_env": "OLLAMA_BASE_URL", "key_env": None,
        "model_env": "OLLAMA_MODEL", "default_model": "llama3.1",
    },
    "custom": {
        "base_url_env": "LLM_FALLBACK_BASE_URL", "key_env": "LLM_FALLBACK_API_KEY",
        "model_env": "LLM_FALLBACK_MODEL", "default_model": "",
        "key_optional": True,
    },
}

_RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504}
_SECRET_PATTERN = re.compile(
    r"(?i)(bearer\s+[A-Za-z0-9._~+/-]+=*|(?:api[_-]?key|key|token)=[^&\s\"']+|"
    r"sk-[A-Za-z0-9_-]{8,}|gsk_[A-Za-z0-9]{8,}|AIza[0-9A-Za-z_-]{10,})")


class LLMError(RuntimeError):
    """A provider failed; ``retryable`` tells callers whether to move on."""

    def __init__(self, provider, message, status_code=None, retryable=True):
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code
        self.retryable = retryable


def redact(text) -> str:
    """Strip anything that looks like a credential from diagnostic text."""
    return _SECRET_PATTERN.sub("[REDACTED]", str(text or ""))


def _provider_config(name: str) -> dict | None:
    spec = _PROVIDERS.get(name)
    if not spec:
        return None
    base_url = spec.get("base_url") or os.getenv(spec.get("base_url_env", ""), "").strip()
    if not base_url:
        return None
    key = os.getenv(spec["key_env"], "").strip() if spec.get("key_env") else ""
    if spec.get("key_env") and not key and not spec.get("key_optional"):
        return None
    model = os.getenv(spec["model_env"], "").strip() or spec.get("default_model", "")
    if not model:
        return None
    return {"name": name, "base_url": base_url.rstrip("/"), "api_key": key, "model": model}


def configured_providers() -> list[dict]:
    """Fallback providers with credentials, in ``LLM_PROVIDER_ORDER``."""
    order = [item.strip().lower() for item in
             os.getenv("LLM_PROVIDER_ORDER", DEFAULT_ORDER).split(",") if item.strip()]
    result, seen = [], set()
    for name in order:
        if name in seen or name == "gemini":
            continue
        seen.add(name)
        config = _provider_config(name)
        if config:
            result.append(config)
    return result


def provider_status() -> list[dict]:
    """Credential presence only (never values) for health pages and docs."""
    rows = [{"provider": "gemini", "configured": bool(os.getenv("GEMINI_API_KEY", "").strip()),
             "role": "primary"}]
    for name in _PROVIDERS:
        rows.append({"provider": name, "configured": _provider_config(name) is not None,
                     "role": "fallback"})
    return rows


def _headers(config: dict) -> dict:
    headers = {"Content-Type": "application/json"}
    if config.get("api_key"):
        headers["Authorization"] = "Bearer " + config["api_key"]
    return headers


def _chat_models(config: dict, timeout: int = 20) -> list[str]:
    """Best-effort model discovery used only after a model-not-found error."""
    try:
        response = requests.get(config["base_url"] + "/models", headers=_headers(config), timeout=timeout)
        if response.status_code != 200:
            return []
        payload = response.json()
    except (requests.RequestException, ValueError):
        return []
    rows = payload.get("data") if isinstance(payload, dict) else payload
    ids = []
    for row in rows or []:
        model_id = str((row.get("id") if isinstance(row, dict) else row) or "")
        lowered = model_id.lower()
        if not model_id or any(term in lowered for term in (
                "embed", "whisper", "tts", "guard", "rerank", "moderation", "vision",
                "image", "audio", "speech", "ocr", "transcribe")):
            continue
        ids.append(model_id)
    preference = ("70b", "large", "pro", "gpt-4", "llama-3", "llama3", "mistral", "qwen", "gemma")

    def rank(model_id):
        lowered = model_id.lower()
        return next((index for index, term in enumerate(preference) if term in lowered),
                    len(preference))
    return sorted(ids, key=rank)


def _post_chat(config: dict, model: str, prompt: str, *, json_mode: bool,
               temperature: float, max_tokens: int, timeout: int) -> str:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    name = config["name"]
    try:
        response = requests.post(config["base_url"] + "/chat/completions",
                                 headers=_headers(config), json=body, timeout=timeout)
    except requests.RequestException as exc:
        raise LLMError(name, f"Network failure calling {name}: {redact(exc)}", retryable=True) from exc
    if response.status_code != 200:
        detail = redact(str(getattr(response, "text", ""))[:400])
        raise LLMError(name, f"{name} HTTP {response.status_code} for model {model}: {detail}",
                       status_code=response.status_code,
                       retryable=response.status_code in _RETRYABLE)
    try:
        data = response.json()
        choice = (data.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""
    except (ValueError, AttributeError, IndexError, TypeError) as exc:
        raise LLMError(name, f"{name} returned an unreadable response.", status_code=200) from exc
    if isinstance(text, list):  # some gateways return content parts
        text = "".join(str(part.get("text", "")) for part in text if isinstance(part, dict))
    if str(choice.get("finish_reason") or "").lower() == "length":
        raise LLMError(name, f"{name} stopped at the output-token limit; the JSON may be truncated.",
                       status_code=200)
    if not str(text).strip():
        raise LLMError(name, f"{name} returned an empty response.", status_code=200)
    return str(text)


def complete(config: dict, prompt: str, *, json_mode: bool = True, temperature: float = 0.45,
             max_tokens: int | None = None, attempts: int = 2, timeout: int = 180) -> str:
    """Call one provider with bounded retries and model discovery on 404/400."""
    max_tokens = max_tokens or int(os.getenv("LLM_FALLBACK_MAX_TOKENS", "8192"))
    model = config["model"]
    discovered = False
    json_supported = json_mode
    last_error: LLMError | None = None
    for attempt in range(max(1, attempts) + 2):
        try:
            return _post_chat(config, model, prompt, json_mode=json_supported,
                              temperature=temperature, max_tokens=max_tokens, timeout=timeout)
        except LLMError as exc:
            last_error = exc
            message = str(exc).lower()
            if exc.status_code == 400 and json_supported and "response_format" in message:
                json_supported = False  # provider/model without JSON mode
                continue
            if exc.status_code in {400, 404} and not discovered and (
                    "model" in message or exc.status_code == 404):
                discovered = True
                candidates = [m for m in _chat_models(config) if m != model]
                if candidates:
                    print(f"{config['name']}: model '{model}' unavailable; trying discovered model '{candidates[0]}'.")
                    model = candidates[0]
                    continue
            if not exc.retryable or attempt >= attempts - 1:
                raise
            delay = min(5 * (2 ** attempt), 30) + random.uniform(0, 1)
            print(f"{config['name']} temporary failure (HTTP {exc.status_code}); retrying in {delay:.0f}s.")
            time.sleep(delay)
    raise last_error or LLMError(config["name"], "request failed")


# Provider and model that served the most recent fallback request (no prompt, no key).
LAST_FALLBACK: dict = {}


def complete_with_fallbacks(prompt: str, providers: list[dict] | None = None, **kwargs) -> str:
    """Try each configured fallback provider in order; raise one combined error."""
    providers = configured_providers() if providers is None else providers
    if not providers:
        raise LLMError("none", "No fallback LLM provider is configured.", retryable=False)
    failures = []
    for config in providers:
        try:
            print(f"Requesting text from fallback provider: {config['name']} ({config['model']})")
            text = complete(config, prompt, **kwargs)
            LAST_FALLBACK.clear()
            LAST_FALLBACK.update({"provider": config["name"], "model": config["model"],
                                  "skipped": list(failures)})
            try:
                from core.monitoring import record_event
                record_event("llm", "fallback_generate", "success",
                             metadata={"provider": config["name"], "model": config["model"]})
            except Exception:
                pass
            return text
        except LLMError as exc:
            failures.append(f"{config['name']}: {redact(exc)}")
            print(f"Fallback provider {config['name']} failed: {redact(exc)}")
    raise LLMError("all", "All fallback LLM providers failed.\n" +
                   "\n".join(f"- {item}" for item in failures))


def discover_gemini_models(api_key: str, timeout: int = 20) -> list[str]:
    """List this key's text models (newest Flash first) after configured IDs 404."""
    if not api_key:
        return []
    try:
        response = requests.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            headers={"x-goog-api-key": api_key}, params={"pageSize": 200}, timeout=timeout)
        if response.status_code != 200:
            return []
        models = response.json().get("models") or []
    except (requests.RequestException, ValueError, AttributeError):
        return []
    names = []
    for model in models:
        if not isinstance(model, dict):
            continue
        if "generateContent" not in (model.get("supportedGenerationMethods") or []):
            continue
        name = str(model.get("name") or "").removeprefix("models/")
        lowered = name.lower()
        if not name.startswith("gemini") or any(term in lowered for term in (
                "embedding", "image", "tts", "audio", "live", "vision", "aqa", "robotics", "computer")):
            continue
        names.append(name)

    def version_key(name):
        match = re.search(r"gemini-(\d+)(?:\.(\d+))?", name)
        version = (int(match.group(1)), int(match.group(2) or 0)) if match else (0, 0)
        flash = 1 if "flash" in name else 0
        stable = 0 if any(term in name for term in ("preview", "exp")) else 1
        lite = 0 if "lite" in name else 1
        return (flash, stable, version, lite)
    return sorted(dict.fromkeys(names), key=version_key, reverse=True)
