"""Bounded, non-redirecting reads from configured provider endpoints."""

from __future__ import annotations

import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Treat redirects as errors; credentials and data stay on the first hop."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# A localhost HTTP endpoint must never travel through an inherited proxy.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)


def read_limited(response, limit: int) -> bytes:
    raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("provider response too large")
    return raw
