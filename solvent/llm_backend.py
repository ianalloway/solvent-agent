"""
llm_backend.py: optional OpenAI-compatible backend (Ollama, LM Studio, vLLM, ...).

Opt-in only. Nothing here runs unless ``SOLVENT_LLM_BASE_URL`` or
``SOLVENT_LLM_MODEL`` is set, so the default zero-key demo is unchanged.

    export SOLVENT_LLM_MODEL=llama3.2                      # Ollama default URL
    export SOLVENT_LLM_BASE_URL=http://localhost:11434/v1  # optional
    export SOLVENT_LLM_API_KEY=...                         # optional, sent as Bearer
    export SOLVENT_LLM_TIMEOUT=30                          # seconds, optional

Standard library only (urllib). The API key is only ever placed in the
Authorization header; it is never logged, echoed in errors, or written to the brief.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://localhost:11434/v1"
DEFAULT_MODEL = "llama3.2"
DEFAULT_TIMEOUT = 30.0
MAX_RESPONSE_BYTES = 2_000_000


class LLMBackendError(RuntimeError):
    """A failed call. The message never contains the API key or response body."""


def enabled() -> bool:
    return bool(
        os.environ.get("SOLVENT_LLM_BASE_URL", "").strip()
        or os.environ.get("SOLVENT_LLM_MODEL", "").strip()
    )


def base_url() -> str:
    return (os.environ.get("SOLVENT_LLM_BASE_URL", "").strip() or DEFAULT_BASE_URL).rstrip("/")


def model() -> str:
    return os.environ.get("SOLVENT_LLM_MODEL", "").strip() or DEFAULT_MODEL


def timeout() -> float:
    raw = os.environ.get("SOLVENT_LLM_TIMEOUT", "").strip()
    try:
        value = float(raw) if raw else DEFAULT_TIMEOUT
    except ValueError:
        return DEFAULT_TIMEOUT
    return value if 0 < value <= 600 else DEFAULT_TIMEOUT


def describe() -> str:
    """Safe, key-free description for logs and `solvent doctor`."""
    key = "yes" if os.environ.get("SOLVENT_LLM_API_KEY", "").strip() else "no"
    return f"{model()} @ {base_url()} (api key set: {key})"


def complete(system: str, user: str) -> tuple[str, dict]:
    """One chat completion. Returns (text, usage). Raises LLMBackendError on any failure."""
    url = base_url() + "/chat/completions"
    if not url.startswith(("http://", "https://")):
        raise LLMBackendError("SOLVENT_LLM_BASE_URL must start with http:// or https://")
    body = json.dumps(
        {
            "model": model(),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.4,
            "max_tokens": 1200,
            "stream": False,
        }
    ).encode()
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    key = os.environ.get("SOLVENT_LLM_API_KEY", "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout()) as r:  # noqa: S310 (scheme checked)
            raw = r.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise LLMBackendError(f"HTTP {exc.code} from LLM backend") from None
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        raise LLMBackendError(f"LLM backend unreachable ({type(exc).__name__})") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise LLMBackendError("LLM backend response too large")
    try:
        data = json.loads(raw)
        text = data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise LLMBackendError("malformed response from LLM backend") from None
    if not isinstance(text, str) or not text.strip():
        raise LLMBackendError("empty completion from LLM backend")
    usage = data.get("usage") if isinstance(data, dict) else None
    if isinstance(usage, dict) and usage:
        return text, {
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
            "estimated": False,
        }
    return text, {}
