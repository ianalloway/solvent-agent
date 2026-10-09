"""Request-origin and abuse-limiting helpers for the HTTP server.

Two small, framework-free pieces that ``server.py`` builds its gates on:

* :func:`is_local_request` -- the "is this really the operator on this machine"
  check behind the local-only routes.  Trusting the peer address alone is wrong
  behind a reverse proxy (every proxied request arrives from 127.0.0.1), so a
  request that carries proxy headers is never treated as local.
* :class:`FailureLimiter` -- a tiny in-memory failed-attempt limiter used to slow
  down guessing of single-use pairing tokens.
"""

from __future__ import annotations

import ipaddress
import os
import threading
import time
from collections import deque
from collections.abc import Mapping
from typing import Any

#: Environment variable that controls how "local" is decided.
#:
#: * ``auto`` (default): the peer address is loopback **and** the request carries
#:   no proxy/forwarding header.
#: * ``never``: nothing is local.  Local-only routes then need a delivery token
#:   (or the dashboard token).  Use this when a reverse proxy fronts the server.
#: * ``peer``: legacy behaviour -- trust the peer address only.  Only safe when
#:   the server is *not* behind a proxy.
LOCAL_ACCESS_ENV = "SOLVENT_LOCAL_ACCESS"

#: Headers that a reverse proxy / CDN adds.  If any is present the request did not
#: originate on this machine, whatever the peer address says.
PROXY_HEADERS = (
    "forwarded",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-forwarded-port",
    "x-forwarded-server",
    "x-real-ip",
    "x-client-ip",
    "x-cluster-client-ip",
    "cf-connecting-ip",
    "true-client-ip",
    "via",
)

# "testclient" is the peer name Starlette's TestClient reports.
_LOCAL_NAMES = frozenset({"localhost", "testclient"})


def _is_loopback(host: str) -> bool:
    if host.lower() in _LOCAL_NAMES:
        return True
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback


def local_access_mode(env: Mapping[str, str] | None = None) -> str:
    """The configured local-access mode: ``auto`` (default), ``never`` or ``peer``."""
    raw = (env if env is not None else os.environ).get(LOCAL_ACCESS_ENV, "auto")
    mode = raw.strip().lower()
    return mode if mode in ("auto", "never", "peer") else "auto"


def peer_host(request: Any) -> str:
    """The peer (socket) address of a request, or ``""``."""
    client = getattr(request, "client", None)
    return str(getattr(client, "host", "") or "") if client else ""


def has_proxy_headers(request: Any) -> bool:
    headers = getattr(request, "headers", None) or {}
    return any(name in headers for name in PROXY_HEADERS)


def is_local_request(request: Any, env: Mapping[str, str] | None = None) -> bool:
    """True only for a request that genuinely originates on this machine."""
    if request is None:
        return False
    mode = local_access_mode(env)
    if mode == "never":
        return False
    if not _is_loopback(peer_host(request)):
        return False
    if mode == "peer":
        return True
    return not has_proxy_headers(request)


class FailureLimiter:
    """Block a key after ``max_failures`` failures inside a sliding ``window`` (seconds)."""

    def __init__(self, max_failures: int, window: float, max_keys: int = 10_000):
        self.max_failures = max_failures
        self.window = window
        self.max_keys = max_keys
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if not hits:
            self._hits.pop(key, None)
        return hits

    def blocked(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            return len(self._prune(key, now)) >= self.max_failures

    def record_failure(self, key: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            if key not in self._hits and len(self._hits) >= self.max_keys:
                # Bound memory: drop the stalest key.
                oldest = min(self._hits, key=lambda k: self._hits[k][-1] if self._hits[k] else 0)
                self._hits.pop(oldest, None)
            self._hits.setdefault(key, deque()).append(now)
