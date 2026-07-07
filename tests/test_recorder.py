"""Tests for the Recorder — sounddevice.InputStream is mocked so no mic is needed."""

import numpy as np
import pytest
import sounddevice

from recorder import Recorder


def _make_fake_stream(store, start=None):
    """Return a FakeInputStream class that records the created instance in `store`."""

    class FakeInputStream:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]
            self.started = False
            self.closed = False
            store["stream"] = self

        def start(self):
            if start is not None:
                start(self)
            self.started = True

        def stop(self):
            self.started = False

        def close(self):
            self.closed = True

    return FakeInputStream


def test_stop_returns_concatenated_1d_float32_array(monkeypatch):
    store = {}
    monkeypatch.setattr(sounddevice, "InputStream", _make_fake_stream(store))

    rec = Recorder()
    rec.start()
    assert rec.is_recording is True

    stream = store["stream"]
    # Frames arrive shaped (n, 1) from a mono InputStream.
    stream.callback(np.array([[0.1], [0.2]], dtype=np.float32), 2, None, None)
    stream.callback(np.array([[0.3]], dtype=np.float32), 1, None, None)

    audio = rec.stop()

    assert isinstance(audio, np.ndarray)
    assert audio.dtype == np.float32
    assert audio.ndim == 1
    assert np.allclose(audio, [0.1, 0.2, 0.3])
    assert rec.is_recording is False
    assert stream.closed is True


def test_stop_with_no_frames_returns_none(monkeypatch):
    store = {}
    monkeypatch.setattr(sounddevice, "InputStream", _make_fake_stream(store))

    rec = Recorder()
    rec.start()
    audio = rec.stop()

    assert audio is None


def test_start_failure_leaves_recorder_not_recording(monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("no microphone")

    monkeypatch.setattr(sounddevice, "InputStream", boom)

    rec = Recorder()
    with pytest.raises(RuntimeError):
        rec.start()

    assert rec.is_recording is False


def test_start_failure_on_stream_start(monkeypatch):
    """If .start() raises after construction, is_recording must stay False."""
    store = {}

    def raising_start(self):
        raise RuntimeError("device busy")

    monkeypatch.setattr(
        sounddevice, "InputStream", _make_fake_stream(store, start=raising_start)
    )

    rec = Recorder()
    with pytest.raises(RuntimeError):
        rec.start()

    assert rec.is_recording is False
