"""Tests for refiner — urllib is faked so no real Ollama HTTP call happens.

Verifies that a successful response returns the refined text and that every
failure mode (network error, timeout, malformed body, empty response) falls
back to the raw transcript, and that empty input short-circuits with no call.
"""

import json
import string
import urllib.error

from whisper_paste import config
from whisper_paste import refiner


# --------------------------------------------------------------------------- #
# Prompt contract
# --------------------------------------------------------------------------- #
def test_prompt_has_exactly_one_placeholder():
    """A stray '{' in the prompt would raise, but only on the --refine path."""
    fields = [
        field
        for _, field, _, _ in string.Formatter().parse(config.REFINER_PROMPT)
        if field is not None
    ]

    assert fields == ["text"]


def test_prompt_passes_the_transcript_through_verbatim():
    prompt = config.REFINER_PROMPT.format(text="braces {like this} and 100% signs")

    assert "braces {like this} and 100% signs" in prompt


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


def test_formatted_multiline_output_is_preserved(monkeypatch):
    """Paragraphs, list markers and tabs are the point of running the refiner."""
    formatted = "First paragraph.\n\nThen the steps:\n\n1. Do this.\n2. Do that.\n\n- a note\n\tindented"

    def handler(req, timeout):
        return _FakeResponse(json.dumps({"response": formatted}).encode("utf-8"))

    _install_urlopen(monkeypatch, handler)

    assert refiner.refine("first paragraph then the steps do this do that") == formatted


def test_crlf_line_endings_are_normalised(monkeypatch):
    def handler(req, timeout):
        return _FakeResponse(json.dumps({"response": "one\r\ntwo\rthree"}).encode("utf-8"))

    _install_urlopen(monkeypatch, handler)

    assert refiner.refine("one two three") == "one\ntwo\nthree"


def test_escape_sequence_falls_back_to_raw(monkeypatch):
    """ANSI escapes could rewrite a terminal's display once pasted."""
    def handler(req, timeout):
        payload = {"response": "clean text\x1b[2K\x1b[1Ahidden"}
        return _FakeResponse(json.dumps(payload).encode("utf-8"))

    _install_urlopen(monkeypatch, handler)

    assert refiner.refine("clean text") == "clean text"


def test_zero_width_and_bidi_characters_fall_back_to_raw(monkeypatch):
    def handler(req, timeout):
        payload = {"response": "looks​fine‮evil"}
        return _FakeResponse(json.dumps(payload).encode("utf-8"))

    _install_urlopen(monkeypatch, handler)

    assert refiner.refine("looks fine") == "looks fine"


def test_absurdly_long_output_falls_back_to_raw(monkeypatch):
    def handler(req, timeout):
        return _FakeResponse(json.dumps({"response": "spam " * 500}).encode("utf-8"))

    _install_urlopen(monkeypatch, handler)

    assert refiner.refine("hi") == "hi"


def test_non_string_response_falls_back_to_raw(monkeypatch):
    def handler(req, timeout):
        return _FakeResponse(json.dumps({"response": {"unexpected": "shape"}}).encode("utf-8"))

    _install_urlopen(monkeypatch, handler)

    assert refiner.refine("raw transcript") == "raw transcript"


def test_empty_input_returned_without_http_call(monkeypatch):
    def handler(req, timeout):  # pragma: no cover - must never be reached
        raise AssertionError("urlopen should not be called for empty input")

    calls = _install_urlopen(monkeypatch, handler)

    assert refiner.refine("") == ""
    assert calls == []


# --------------------------------------------------------------------------- #
# probe() - startup check of the configured Ollama endpoint
# --------------------------------------------------------------------------- #
def _probe_handler(version_body=None, tags_body=None, error=None):
    """Route /api/version and /api/tags to canned bodies, or raise `error`."""
    def handler(req, timeout):
        if error is not None:
            raise error
        if req.full_url.endswith("/api/version"):
            return _FakeResponse(json.dumps(version_body).encode("utf-8"))
        if req.full_url.endswith("/api/tags"):
            return _FakeResponse(json.dumps(tags_body).encode("utf-8"))
        raise AssertionError(f"unexpected URL {req.full_url}")

    return handler


def test_probe_reports_ready_when_version_and_model_present(monkeypatch):
    _install_urlopen(
        monkeypatch,
        _probe_handler({"version": "0.6.0"}, {"models": [{"name": "gemma3:4b"}]}),
    )

    ok, message = refiner.probe()

    assert ok is True
    assert "0.6.0" in message
    assert "gemma3:4b" in message


def test_probe_reports_model_not_pulled(monkeypatch):
    _install_urlopen(
        monkeypatch,
        _probe_handler({"version": "0.6.0"}, {"models": [{"name": "llama3:8b"}]}),
    )

    ok, message = refiner.probe()

    assert ok is False
    assert "ollama pull gemma3:4b" in message


def test_probe_accepts_model_reported_with_a_tag_suffix(monkeypatch):
    monkeypatch.setattr(refiner, "OLLAMA_MODEL", "gemma3")
    _install_urlopen(
        monkeypatch,
        _probe_handler({"version": "0.6.0"}, {"models": [{"name": "gemma3:latest"}]}),
    )

    ok, _ = refiner.probe()

    assert ok is True


def test_probe_reports_unreachable_endpoint_without_raising(monkeypatch):
    _install_urlopen(
        monkeypatch, _probe_handler(error=urllib.error.URLError("connection refused"))
    )

    ok, message = refiner.probe()

    assert ok is False
    assert "connection refused" in message
