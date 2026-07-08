"""Tests for refiner — urllib is faked so no real Ollama HTTP call happens.

Verifies that a successful response returns the refined text and that every
failure mode (network error, timeout, malformed body, empty response) falls
back to the raw transcript, and that empty input short-circuits with no call.
"""

import json
import urllib.error

import refiner


class _FakeResponse:
    """Minimal context-manager stand-in for urllib.request.urlopen's return."""

    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def _install_urlopen(monkeypatch, handler):
    """Replace urllib.request.urlopen; record the calls it receives."""
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append((req, timeout))
        return handler(req, timeout)

    monkeypatch.setattr(refiner.urllib.request, "urlopen", fake_urlopen)
    return calls


def test_successful_refinement_returns_refined_text(monkeypatch):
    def handler(req, timeout):
        return _FakeResponse(json.dumps({"response": "  Cleaned up text.  "}).encode("utf-8"))

    calls = _install_urlopen(monkeypatch, handler)

    result = refiner.refine("cleaned up text")

    assert result == "Cleaned up text."  # stripped
    assert len(calls) == 1


def test_url_error_returns_raw_text(monkeypatch):
    def handler(req, timeout):
        raise urllib.error.URLError("connection refused")

    _install_urlopen(monkeypatch, handler)

    assert refiner.refine("raw transcript") == "raw transcript"


def test_timeout_returns_raw_text(monkeypatch):
    def handler(req, timeout):
        raise TimeoutError("timed out")

    _install_urlopen(monkeypatch, handler)

    assert refiner.refine("raw transcript") == "raw transcript"


def test_malformed_json_returns_raw_text(monkeypatch):
    def handler(req, timeout):
        return _FakeResponse(b"this is not json{{{")

    _install_urlopen(monkeypatch, handler)

    assert refiner.refine("raw transcript") == "raw transcript"


def test_empty_response_field_returns_raw_text(monkeypatch):
    def handler(req, timeout):
        return _FakeResponse(json.dumps({"response": "   "}).encode("utf-8"))

    _install_urlopen(monkeypatch, handler)

    assert refiner.refine("raw transcript") == "raw transcript"


def test_empty_input_returned_without_http_call(monkeypatch):
    def handler(req, timeout):  # pragma: no cover - must never be reached
        raise AssertionError("urlopen should not be called for empty input")

    calls = _install_urlopen(monkeypatch, handler)

    assert refiner.refine("") == ""
    assert calls == []
