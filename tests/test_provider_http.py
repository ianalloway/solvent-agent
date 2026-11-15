"""Offline tests for the provider transport policy and read limits."""

from io import BytesIO

import pytest

from solvent import llm_backend
from solvent.provider_http import read_limited


def test_response_reader_caps_bytes_before_parsing():
    src = BytesIO(b"x" * 30)
    with pytest.raises(ValueError, match="too large"):
        read_limited(src, 8)
    assert src.tell() == 9


@pytest.mark.parametrize(
    "url", ["https://models.example.test/v1", "http://localhost:11434/v1", "http://[::1]:11434/v1"]
)
def test_provider_transport_supports_https_and_local_runtimes(monkeypatch, url):
    monkeypatch.setenv("SOLVENT_LLM_BASE_URL", url)
    assert llm_backend._completion_url() == url + "/chat/completions"


def test_provider_transport_requires_tls_for_remote_backend(monkeypatch):
    monkeypatch.setenv("SOLVENT_LLM_BASE_URL", "http://models.example.test/v1")
    with pytest.raises(llm_backend.LLMBackendError, match="requires HTTPS"):
        llm_backend._completion_url()
