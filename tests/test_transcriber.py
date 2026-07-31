"""Tests for the transcriber — real whisper backends are replaced via sys.modules."""

import os
import re
import sys
import threading
import time
import types

import numpy as np
import pytest

from whisper_paste import bundle
from whisper_paste import config
from whisper_paste import transcriber


@pytest.fixture(autouse=True)
def reset_state():
    """Reset lazy model globals and restore mutated config flags around each test.

    WHISPER_MODEL is saved/restored too: config is process-global and mutated in
    place, so a test that leaves a model name behind would surface as a failure
    in an unrelated file (tests/test_cli.py).
    """
    transcriber._model = None
    transcriber._backend = None
    saved_gpu = config.USE_GPU
    saved_lang = config.WHISPER_LANGUAGE
    saved_model = config.WHISPER_MODEL
    yield
    transcriber._model = None
    transcriber._backend = None
    config.USE_GPU = saved_gpu
    config.WHISPER_LANGUAGE = saved_lang
    config.WHISPER_MODEL = saved_model


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    """Pose as a PyInstaller onedir bundle rooted at tmp_path (see tests/test_bundle.py)."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "_internal"), raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "WhisperPaste.exe"))
    return tmp_path


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


def test_whisper_cpp_auto_detects_when_language_unset(monkeypatch):
    """No --lang must mean auto-detect on whisper.cpp too, not its 'en' default.

    whisper.cpp's own default for params.language is "en", so omitting the kwarg
    silently forces English. Passing "" is what makes it run language detection
    (it rejects "auto"), matching the faster-whisper branch's language=None.
    """
    config.USE_GPU = True
    config.WHISPER_LANGUAGE = None
    calls = {}

    class FakeModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, media, **kwargs):
            calls["kwargs"] = kwargs
            return [_Seg("hola")]

    _install_whisper_cpp(monkeypatch, FakeModel)

    transcriber.transcribe(np.zeros(10, dtype=np.float32))

    assert calls["kwargs"].get("language") == ""


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


def _install_recording_faster_whisper(monkeypatch, calls):
    class FakeModel:
        def __init__(self, *args, **kwargs):
            calls["init"] = (args, kwargs)

        def transcribe(self, audio, **kwargs):
            return ([], object())

    _install_faster_whisper(monkeypatch, FakeModel)


def test_frozen_loads_the_bundled_model_directory(monkeypatch, frozen):
    """A shipped model must be loaded from disk, not re-downloaded by name.

    WhisperModel accepts a directory as its first argument, which is what makes
    swapping the bare name for the bundled path safe.
    """
    config.USE_GPU = False
    model_dir = frozen / "models" / "small"
    model_dir.mkdir(parents=True)
    config.WHISPER_MODEL = "small"
    calls = {}
    _install_recording_faster_whisper(monkeypatch, calls)

    transcriber._get_model()

    args, kwargs = calls["init"]
    assert os.path.normcase(args[0]) == os.path.normcase(str(model_dir))
    assert kwargs == {"device": "cpu", "compute_type": "int8"}


def test_source_checkout_passes_the_bare_model_name(monkeypatch):
    config.USE_GPU = False
    config.WHISPER_MODEL = "small"
    calls = {}
    _install_recording_faster_whisper(monkeypatch, calls)

    assert bundle.is_frozen() is False

    transcriber._get_model()

    args, kwargs = calls["init"]
    assert args[0] == "small"
    assert kwargs == {"device": "cpu", "compute_type": "int8"}


def test_frozen_falls_back_to_download_for_a_model_that_did_not_ship(monkeypatch, frozen):
    """`--model medium` on a ZIP built with `small`: the name must reach WhisperModel."""
    config.USE_GPU = False
    (frozen / "models" / "small").mkdir(parents=True)
    config.WHISPER_MODEL = "medium"
    calls = {}
    _install_recording_faster_whisper(monkeypatch, calls)

    transcriber._get_model()

    args, _kwargs = calls["init"]
    assert args[0] == "medium"


def test_gpu_is_rejected_when_frozen_before_importing_pywhispercpp(monkeypatch, frozen):
    """--gpu must fail with the actionable message, not a bare ModuleNotFoundError.

    A perfectly working fake pywhispercpp is installed on purpose: the model
    never being constructed is what proves the check runs *before* the import,
    which is the only placement where a source-built Vulkan wheel is irrelevant.
    """
    config.USE_GPU = True
    constructed = []

    class FakeModel:
        def __init__(self, *args, **kwargs):
            constructed.append(args)

        def transcribe(self, media, **kwargs):
            return []

    _install_whisper_cpp(monkeypatch, FakeModel)

    with pytest.raises(RuntimeError, match=re.escape("not available in the portable build")):
        transcriber._get_model()

    assert constructed == []
    assert transcriber._model is None


def test_gpu_from_source_still_loads_whisper_cpp(monkeypatch):
    """The frozen guard must not touch the source path --gpu was built for."""
    config.USE_GPU = True
    config.WHISPER_MODEL = "small"
    calls = {}

    class FakeModel:
        def __init__(self, *args, **kwargs):
            calls["init"] = (args, kwargs)

        def transcribe(self, media, **kwargs):
            return []

    _install_whisper_cpp(monkeypatch, FakeModel)

    assert bundle.is_frozen() is False

    transcriber._get_model()

    assert transcriber._backend == "whisper.cpp"
    assert calls["init"][0] == ("small",)
