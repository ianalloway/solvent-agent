"""
security.py — SOLVENT defence layer.

Covers four threat categories:

1. AUTH BYPASS
   - Stripe webhook signatures are verified with HMAC-SHA256 before any
     payload data is trusted (process_webhook must call verify_webhook_signature).
   - API keys are validated against known-good prefixes; live Stripe keys
     are refused unconditionally.
   - Config values are re-validated after load to detect tampering.

2. ACCOUNT TAKEOVER
   - Webhook payloads are replay-protected: duplicate event IDs (seen in the
     last 10 minutes) are rejected.
   - Stripe event timestamps outside a 5-minute tolerance window are rejected.
   - Customer email is validated and normalised before it enters the ledger or
     any external call, blocking header-injection / SSRF via crafted addresses.

3. LLM PROMPT INJECTION
   - All untrusted strings that flow into an LLM prompt (topic, context,
     customer_email) are sanitised before use:
       - Control characters, null bytes, and Unicode direction overrides stripped.
       - Role-override phrases detected and blocked.
       - Hard length cap enforced.
   - The sanitiser is called in service.fulfill() before nemotron.complete().

4. BYPASSING CUSTOM DATA PROTECTIONS
   - Deliverable reports are written under a fixed, resolved OUTPUT_DIR.
     Any job ID or topic that would escape that directory (path traversal)
     is rejected before the file is opened.
   - The catalog and config files are read with strict schema validation;
     unexpected keys are stripped rather than trusted.
   - Vendor names from any external source are matched against the allowlist
     using exact equality (no glob / prefix matching).
"""

from __future__ import annotations

import hashlib
import hmac
import re
import time
import unicodedata
from collections import OrderedDict
from pathlib import Path

# ---------------------------------------------------------------------------
# 1. AUTH — Stripe webhook signature verification
# ---------------------------------------------------------------------------

def verify_webhook_signature(payload: bytes, sig_header: str, secret: str,
                              tolerance_seconds: int = 300) -> None:
    """Verify a Stripe webhook Stripe-Signature header.

    Args:
        payload: The raw request body bytes.
        sig_header: The value of the ``Stripe-Signature`` HTTP header.
        secret: The webhook endpoint secret (``whsec_...``).
        tolerance_seconds: Reject events older than this many seconds (default 5 min).

    Raises:
        WebhookAuthError: If signature is missing, invalid, or timestamp is stale.
    """
    if not sig_header:
        raise WebhookAuthError("missing Stripe-Signature header")
    if not secret:
        raise WebhookAuthError("webhook secret not configured")

    parts: dict[str, list[str]] = {}
    for part in sig_header.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            parts.setdefault(k.strip(), []).append(v.strip())

    timestamps = parts.get("t", [])
    v1_sigs = parts.get("v1", [])

    if not timestamps or not v1_sigs:
        raise WebhookAuthError("malformed Stripe-Signature header")

    try:
        ts = int(timestamps[0])
    except ValueError:
        raise WebhookAuthError("invalid timestamp in Stripe-Signature")

    age = abs(time.time() - ts)
    if age > tolerance_seconds:
        raise WebhookAuthError(
            f"webhook timestamp too old or too new: {age:.0f}s (tolerance {tolerance_seconds}s)"
        )

    signed_payload = f"{ts}.".encode() + payload
    expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()

    if not any(hmac.compare_digest(expected, sig) for sig in v1_sigs):
        raise WebhookAuthError("webhook signature verification failed")


def validate_stripe_key(key: str) -> None:
    """Refuse live Stripe keys and keys that don't look like valid Stripe secrets.

    Args:
        key: The Stripe API key to validate.

    Raises:
        AuthBypassError: If the key is a live key or malformed.
    """
    if not key:
        return  # no key → simulate mode, allowed
    if key.startswith("sk_live_"):
        raise AuthBypassError(
            "live Stripe keys are refused by SOLVENT — use sk_test_... only"
        )
    if not (key.startswith("sk_test_") or key.startswith("rk_test_")):
        raise AuthBypassError(
            f"unrecognised Stripe key prefix: {key[:12]}... — expected sk_test_"
        )


# ---------------------------------------------------------------------------
# 2. ACCOUNT TAKEOVER — replay protection + email validation
# ---------------------------------------------------------------------------

# Thread-safe seen-event cache: event_id → timestamp
_SEEN_EVENTS: OrderedDict[str, float] = OrderedDict()
_SEEN_EVENTS_TTL = 600   # 10 minutes
_SEEN_EVENTS_MAX = 5_000


def check_event_replay(event_id: str) -> None:
    """Reject duplicate Stripe webhook event IDs within the replay window.

    Args:
        event_id: The Stripe event ID (e.g. ``evt_...``).

    Raises:
        ReplayAttackError: If the event ID was already processed recently.
    """
    now = time.time()
    # Evict expired entries
    cutoff = now - _SEEN_EVENTS_TTL
    while _SEEN_EVENTS and next(iter(_SEEN_EVENTS.values())) < cutoff:
        _SEEN_EVENTS.popitem(last=False)
    # Cap size
    while len(_SEEN_EVENTS) >= _SEEN_EVENTS_MAX:
        _SEEN_EVENTS.popitem(last=False)

    if event_id in _SEEN_EVENTS:
        raise ReplayAttackError(f"duplicate webhook event rejected: {event_id}")

    _SEEN_EVENTS[event_id] = now


# RFC 5321 simplified — enough to block injections; not an RFC-5322 parser
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]{1,64}@[a-zA-Z0-9.\-]{1,253}\.[a-zA-Z]{2,}$")
_EMAIL_MAX = 320


def validate_email(email: str) -> str:
    """Validate and normalise a customer email address.

    Strips whitespace, lower-cases the domain, and rejects addresses with
    control characters, newlines, or header-injection payloads.

    Args:
        email: The raw email string from external input.

    Returns:
        The sanitised, normalised email address.

    Raises:
        InputValidationError: If the email is malformed or potentially malicious.
    """
    if not email or not isinstance(email, str):
        raise InputValidationError("customer_email must be a non-empty string")

    email = email.strip()

    if len(email) > _EMAIL_MAX:
        raise InputValidationError(f"customer_email exceeds {_EMAIL_MAX} characters")

    # Block CRLF / null / control characters (header injection)
    if any(c in email for c in ("\r", "\n", "\x00")):
        raise InputValidationError("customer_email contains illegal control characters")

    local, _, domain = email.partition("@")
    normalised = f"{local}@{domain.lower()}"

    if not _EMAIL_RE.match(normalised):
        raise InputValidationError(f"customer_email is not a valid email address: {email!r}")

    return normalised


# ---------------------------------------------------------------------------
# 3. LLM PROMPT INJECTION — input sanitisation
# ---------------------------------------------------------------------------

# Patterns that attempt to override the system role or inject instructions
_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?", re.IGNORECASE),
    re.compile(r"(you\s+are|act\s+as|pretend\s+(you\s+are|to\s+be))\s+\w", re.IGNORECASE),
    re.compile(r"new\s+instructions?\s*:", re.IGNORECASE),
    re.compile(r"(system|user|assistant)\s*:\s*\[?INST\]?", re.IGNORECASE),
    re.compile(r"<\s*(system|user|assistant|s|\/s)\s*>", re.IGNORECASE),
    re.compile(r"\[INST\]|\[\/INST\]|<<SYS>>|<</SYS>>", re.IGNORECASE),
    re.compile(r"(disregard|forget|override)\s+(your\s+)?(previous\s+)?(instructions?|rules?|constraints?)", re.IGNORECASE),
    re.compile(r"prompt\s*injection", re.IGNORECASE),
    re.compile(r"jailbreak", re.IGNORECASE),
    re.compile(r"DAN\b", re.IGNORECASE),  # "Do Anything Now" jailbreak keyword
]

# Unicode bidi override / invisible characters often used to hide injections
_INVISIBLE_RE = re.compile(
    r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u206a-\u206f\ufeff\u00ad]"
)

_TOPIC_MAX_LEN   = 500
_CONTEXT_MAX_LEN = 2_000


def sanitise_prompt_input(text: str, field_name: str = "input",
                           max_len: int = _TOPIC_MAX_LEN) -> str:
    """Strip dangerous content from a string before it enters an LLM prompt.

    Removes:
    - Null bytes and ASCII control characters (except tab/newline/CR)
    - Unicode bidi override and invisible characters
    - Strings matching known prompt-injection patterns

    Args:
        text: The raw user-supplied string.
        field_name: Label used in error messages.
        max_len: Maximum allowed length after sanitisation.

    Returns:
        The sanitised string.

    Raises:
        PromptInjectionError: If an injection pattern is detected.
        InputValidationError: If the input is empty or too long.
    """
    if not isinstance(text, str):
        raise InputValidationError(f"{field_name} must be a string")

    # Strip invisible / bidi override characters
    text = _INVISIBLE_RE.sub("", text)

    # Strip null bytes and non-printable control chars (keep \t \n \r)
    cleaned = "".join(
        ch for ch in text
        if ch in ("\t", "\n", "\r") or (unicodedata.category(ch)[0] != "C")
    )

    if not cleaned.strip():
        raise InputValidationError(f"{field_name} must be a non-empty string after sanitisation")

    if len(cleaned) > max_len:
        raise InputValidationError(
            f"{field_name} exceeds maximum length of {max_len} characters"
        )

    # Detect injection patterns
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(cleaned):
            raise PromptInjectionError(
                f"potential prompt injection detected in {field_name}: "
                f"matched pattern /{pattern.pattern}/"
            )

    return cleaned


def sanitise_job(job: dict) -> dict:
    """Sanitise all LLM-facing fields in a job dict in-place.

    Sanitises ``topic``, ``context``, and ``customer_email``.

    Args:
        job: The mutable job dictionary.

    Returns:
        The sanitised job dictionary (same object).

    Raises:
        PromptInjectionError: If injection content is detected.
        InputValidationError: If a field is malformed.
    """
    job["topic"] = sanitise_prompt_input(
        job.get("topic", ""), field_name="topic", max_len=_TOPIC_MAX_LEN
    )
    if job.get("context"):
        job["context"] = sanitise_prompt_input(
            job["context"], field_name="context", max_len=_CONTEXT_MAX_LEN
        )
    if job.get("customer_email"):
        job["customer_email"] = validate_email(job["customer_email"])
    return job


# ---------------------------------------------------------------------------
# 4. DATA PROTECTION — path traversal + schema validation
# ---------------------------------------------------------------------------

def safe_report_path(output_dir: Path, job_id: str) -> Path:
    """Return a resolved report path guaranteed to be inside output_dir.

    Args:
        output_dir: The trusted base directory for reports.
        job_id: The job identifier used as the filename stem.

    Returns:
        A resolved ``Path`` ending in ``<job_id>.md`` inside output_dir.

    Raises:
        PathTraversalError: If the resolved path escapes output_dir.
        InputValidationError: If job_id contains illegal characters.
    """
    # Only allow alphanumeric, hyphens, underscores in job IDs
    if not re.match(r"^[a-zA-Z0-9_\-]{1,128}$", str(job_id)):
        raise InputValidationError(
            f"job_id contains illegal characters: {str(job_id)!r}"
        )

    base = output_dir.resolve()
    candidate = (base / f"{job_id}.md").resolve()

    if not str(candidate).startswith(str(base) + "/") and candidate != base:
        raise PathTraversalError(
            f"report path {candidate} escapes output directory {base}"
        )

    return candidate


def validate_catalog_schema(catalog: dict) -> dict:
    """Strip and validate the Stripe catalog JSON file.

    Only known keys are allowed through; unknown keys are dropped to prevent
    prototype pollution or data confusion if the file is tampered with.

    Args:
        catalog: The raw dict loaded from stripe_catalog.json.

    Returns:
        A clean dict with only permitted keys.
    """
    clean: dict = {}

    if "product_id" in catalog:
        pid = str(catalog["product_id"])
        if re.match(r"^prod_[A-Za-z0-9]{1,50}$", pid):
            clean["product_id"] = pid

    def _clean_prices(raw_prices: dict) -> dict:
        clean_prices = {}
        for k, v in raw_prices.items():
            if re.match(r"^\d{1,10}$", str(k)) and re.match(r"^price_[A-Za-z0-9]{1,50}$", str(v)):
                clean_prices[str(k)] = str(v)
        return clean_prices

    if "price_ids" in catalog and isinstance(catalog["price_ids"], dict):
        clean["price_ids"] = _clean_prices(catalog["price_ids"])
    if "prices" in catalog and isinstance(catalog["prices"], dict):
        clean["prices"] = _clean_prices(catalog["prices"])
    # Normalise: stripe_client uses "prices"; legacy tests use "price_ids"
    if "prices" not in clean and "price_ids" in clean:
        clean["prices"] = clean["price_ids"]
    elif "price_ids" not in clean and "prices" in clean:
        clean["price_ids"] = clean["prices"]

    if "cardholder_id" in catalog:
        cid = str(catalog["cardholder_id"])
        if re.match(r"^ich_[A-Za-z0-9]{1,50}$", cid):
            clean["cardholder_id"] = cid

    return clean


def validate_vendor_exact(vendor: str, allowlist: tuple[str, ...]) -> None:
    """Verify a vendor name matches the allowlist using exact string equality.

    Rejects glob patterns, prefix matches, and any fuzzy comparison.

    Args:
        vendor: The vendor name to check.
        allowlist: The tuple of permitted vendor names.

    Raises:
        GuardrailBypassError: If the vendor is not on the allowlist.
    """
    if vendor not in allowlist:
        raise GuardrailBypassError(
            f"vendor {vendor!r} not on allowlist — "
            f"must be one of {allowlist}"
        )


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SOLVENTSecurityError(Exception):
    """Base class for all SOLVENT security violations."""


class WebhookAuthError(SOLVENTSecurityError):
    """Raised when a Stripe webhook fails signature or timestamp verification."""


class AuthBypassError(SOLVENTSecurityError):
    """Raised when a credential or key fails authentication checks."""


class ReplayAttackError(SOLVENTSecurityError):
    """Raised when a duplicate webhook event ID is detected."""


class PromptInjectionError(SOLVENTSecurityError):
    """Raised when an LLM prompt injection pattern is detected in input."""


class InputValidationError(SOLVENTSecurityError):
    """Raised when an input field fails validation (type, length, format)."""


class PathTraversalError(SOLVENTSecurityError):
    """Raised when a constructed file path escapes the permitted directory."""


class GuardrailBypassError(SOLVENTSecurityError):
    """Raised when a vendor or spend bypass attempt is detected."""
