"""Tests for the transcriber — real whisper backends are replaced via sys.modules."""

import sys
import threading
import time
import types

import numpy as np
import pytest

from whisper_paste import config
from whisper_paste import transcriber


@pytest.fixture(autouse=True)
def reset_state():
    """Reset lazy model globals and restore mutated config flags around each test."""
    transcriber._model = None
    transcriber._backend = None
    saved_gpu = config.USE_GPU
    saved_lang = config.WHISPER_LANGUAGE
    yield
    transcriber._model = None
    transcriber._backend = None
    config.USE_GPU = saved_gpu
    config.WHISPER_LANGUAGE = saved_lang


class _Seg:
    def __init__(self, text):
        self.text = text


def _install_faster_whisper(monkeypatch, model_cls):
    mod = types.ModuleType("faster_whisper")
    mod.WhisperModel = model_cls
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)


def _install_whisper_cpp(monkeypatch, model_cls):
    pkg = types.ModuleType("pywhispercpp")
    model_mod = types.ModuleType("pywhispercpp.model")
    model_mod.Model = model_cls
    pkg.model = model_mod
    monkeypatch.setitem(sys.modules, "pywhispercpp", pkg)
    monkeypatch.setitem(sys.modules, "pywhispercpp.model", model_mod)


def test_faster_whisper_branch(monkeypatch):
    config.USE_GPU = False
    config.WHISPER_LANGUAGE = "en"
    calls = {}

    class FakeModel:
        def __init__(self, *args, **kwargs):
            calls["init"] = (args, kwargs)

        def transcribe(self, audio, language=None, beam_size=None, vad_filter=None):
            calls["audio"] = audio
            calls["language"] = language
            calls["beam_size"] = beam_size
            calls["vad_filter"] = vad_filter
            return ([_Seg("hello"), _Seg("world")], object())

    _install_faster_whisper(monkeypatch, FakeModel)

    audio = np.zeros(10, dtype=np.float32)
    result = transcriber.transcribe(audio)

    assert result == "hello world"
    assert transcriber._backend == "faster-whisper"
    assert calls["audio"] is audio
    assert calls["language"] == "en"
    assert calls["beam_size"] == config.BEAM_SIZE
    assert calls["vad_filter"] is True


def test_whisper_cpp_branch(monkeypatch):
    config.USE_GPU = True
    config.WHISPER_LANGUAGE = "fr"
    calls = {}

    class FakeModel:
        def __init__(self, *args, **kwargs):
            calls["init"] = (args, kwargs)

        def transcribe(self, media, **kwargs):
            calls["media"] = media
            calls["kwargs"] = kwargs
            return [_Seg("bonjour"), _Seg("monde")]

    _install_whisper_cpp(monkeypatch, FakeModel)

    audio = np.zeros(10, dtype=np.float32)
    result = transcriber.transcribe(audio)

    assert result == "bonjour monde"
    assert transcriber._backend == "whisper.cpp"
    assert calls["media"] is audio
    assert calls["kwargs"].get("language") == "fr"


def test_model_loaded_exactly_once_under_concurrency(monkeypatch):
    config.USE_GPU = False
    config.WHISPER_LANGUAGE = None
    init_count = {"n": 0}

    class SlowModel:
        def __init__(self, *args, **kwargs):
            # Sleep to widen the race window so a missing lock would load twice.
            time.sleep(0.05)
            init_count["n"] += 1

        def transcribe(self, audio, **kwargs):
            return ([], object())

    _install_faster_whisper(monkeypatch, SlowModel)

    audio = np.zeros(10, dtype=np.float32)
    errors = []

    def worker():
        try:
            transcriber.transcribe(audio)
        except Exception as exc:  # pragma: no cover - surfaced via assert below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert init_count["n"] == 1


def test_preload_runs_one_warmup(monkeypatch):
    config.USE_GPU = False
    config.WHISPER_LANGUAGE = None
    calls = {"n": 0, "len": None}

    class FakeModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, audio, **kwargs):
            calls["n"] += 1
            calls["len"] = len(audio)
            return ([], object())

    _install_faster_whisper(monkeypatch, FakeModel)

    transcriber.preload()

    assert calls["n"] == 1
    assert calls["len"] == 8000


def test_preload_warmup_failure_does_not_raise(monkeypatch):
    config.USE_GPU = False
    config.WHISPER_LANGUAGE = None

    class FakeModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, audio, **kwargs):
            raise RuntimeError("warm-up boom")

    _install_faster_whisper(monkeypatch, FakeModel)

    # Must not raise despite the transcription failure.
    transcriber.preload()
    assert transcriber._model is not None
