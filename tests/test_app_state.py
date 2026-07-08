"""Tests for the app.py state machine — heavy collaborators are monkeypatched.

`import app` is import-side-effect-light (keyboard/pystray/PIL import cleanly and
no hotkey/tray is started at import time), so we import it directly and replace
its collaborators (`transcribe`, `paste_text`, `refine`, `recorder`,
`update_tray`) plus `threading.Thread`/`threading.Timer` per test.
"""

import pytest

from whisper_paste import app
from whisper_paste import config


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #
class FakeRecorder:
    def __init__(self, audio="AUDIO", start_error=None):
        self._recording = False
        self.audio = audio
        self.start_error = start_error
        self.start_calls = 0
        self.stop_calls = 0

    @property
    def is_recording(self):
        return self._recording

    def start(self):
        self.start_calls += 1
        if self.start_error is not None:
            raise self.start_error
        self._recording = True

    def stop(self):
        self.stop_calls += 1
        self._recording = False
        return self.audio


class SyncThread:
    """Runs the target synchronously on .start() so the worker completes inline."""

    def __init__(self, target=None, daemon=None, **kwargs):
        self._target = target

    def start(self):
        if self._target is not None:
            self._target()


class NullThread:
    """Captures the worker target but never runs it (worker stays 'in flight')."""

    def __init__(self, target=None, daemon=None, **kwargs):
        self._target = target

    def start(self):
        pass


class FakeTimer:
    """Deterministic stand-in for threading.Timer — fire via .function()."""

    instances = []

    def __init__(self, interval, function, args=None, kwargs=None):
        self.interval = interval
        self.function = function
        self.cancelled = False
        self.started = False
        self.daemon = False
        FakeTimer.instances.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def reset_app_state(monkeypatch):
    saved_refiner = config.USE_REFINER
    app.processing = False
    app.tray_icon = None
    app._record_timer = None
    app._shutting_down = False
    FakeTimer.instances.clear()
    # Never let a real Timer/Thread leak from a test that forgets to patch.
    monkeypatch.setattr(app.threading, "Timer", FakeTimer)
    yield
    app.processing = False
    app.tray_icon = None
    app._record_timer = None
    app._shutting_down = False
    config.USE_REFINER = saved_refiner


class FakeTrayIcon:
    """Records stop() calls for shutdown tests."""

    def __init__(self):
        self.stop_calls = 0

    def stop(self):
        self.stop_calls += 1


def _capture_tray(monkeypatch):
    tips = []
    monkeypatch.setattr(app, "update_tray", lambda state, tooltip=None: tips.append((state, tooltip)))
    return tips


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_hotkey_ignored_while_processing(monkeypatch):
    rec = FakeRecorder()
    monkeypatch.setattr(app, "recorder", rec)
    _capture_tray(monkeypatch)
    app.processing = True

    app.on_hotkey()

    assert rec.start_calls == 0
    assert rec.stop_calls == 0


def test_idle_hotkey_starts_recording(monkeypatch):
    rec = FakeRecorder()
    monkeypatch.setattr(app, "recorder", rec)
    _capture_tray(monkeypatch)

    app.on_hotkey()

    assert rec.is_recording is True
    assert rec.start_calls == 1
    # A safety-cap timer is armed on the configured interval.
    assert FakeTimer.instances[-1].interval == config.MAX_RECORD_SECONDS
    assert FakeTimer.instances[-1].started is True


def test_recording_hotkey_processes_and_pastes(monkeypatch):
    rec = FakeRecorder(audio="AUDIO")
    rec._recording = True
    monkeypatch.setattr(app, "recorder", rec)
    _capture_tray(monkeypatch)
    monkeypatch.setattr(app.threading, "Thread", SyncThread)
    monkeypatch.setattr(config, "USE_REFINER", False)

    seen = {}
    monkeypatch.setattr(app, "transcribe", lambda audio: seen.setdefault("audio", audio) and None or "hello")
    pasted = {}
    monkeypatch.setattr(app, "paste_text", lambda text: pasted.__setitem__("text", text))

    app.on_hotkey()

    assert seen["audio"] == "AUDIO"
    assert pasted["text"] == "hello"
    assert app.processing is False
    assert rec.stop_calls == 1


def test_recording_hotkey_runs_refiner_when_enabled(monkeypatch):
    rec = FakeRecorder(audio="AUDIO")
    rec._recording = True
    monkeypatch.setattr(app, "recorder", rec)
    _capture_tray(monkeypatch)
    monkeypatch.setattr(app.threading, "Thread", SyncThread)
    monkeypatch.setattr(config, "USE_REFINER", True)
    monkeypatch.setattr(app, "transcribe", lambda audio: "raw text")
    monkeypatch.setattr(app, "refine", lambda text: text.upper())
    pasted = {}
    monkeypatch.setattr(app, "paste_text", lambda text: pasted.__setitem__("text", text))

    app.on_hotkey()

    assert pasted["text"] == "RAW TEXT"
    assert app.processing is False


def test_mic_error_stays_idle_and_recovers(monkeypatch):
    rec = FakeRecorder(start_error=RuntimeError("no mic"))
    monkeypatch.setattr(app, "recorder", rec)
    tips = _capture_tray(monkeypatch)

    app.on_hotkey()

    assert app.processing is False
    assert rec.is_recording is False
    assert rec.stop_calls == 0
    assert any(
        state == "idle" and tooltip and "Mic error" in tooltip for state, tooltip in tips
    )

    # Next press works normally once the mic recovers.
    rec.start_error = None
    app.on_hotkey()
    assert rec.is_recording is True
    assert rec.start_calls == 2


def test_auto_stop_timer_fires_exactly_once(monkeypatch):
    rec = FakeRecorder(audio="AUDIO")
    monkeypatch.setattr(app, "recorder", rec)
    _capture_tray(monkeypatch)
    monkeypatch.setattr(app.threading, "Thread", SyncThread)
    monkeypatch.setattr(config, "USE_REFINER", False)
    monkeypatch.setattr(app, "transcribe", lambda audio: "hi")
    monkeypatch.setattr(app, "paste_text", lambda text: None)

    app.on_hotkey()  # start recording
    assert rec.is_recording is True
    timer = FakeTimer.instances[-1]

    timer.function()  # cap fires -> process (synchronously)
    assert rec.stop_calls == 1
    assert app.processing is False

    # A second (spurious) fire must not process again.
    timer.function()
    assert rec.stop_calls == 1


def test_manual_stop_cancels_timer(monkeypatch):
    rec = FakeRecorder(audio="AUDIO")
    monkeypatch.setattr(app, "recorder", rec)
    _capture_tray(monkeypatch)
    monkeypatch.setattr(app.threading, "Thread", SyncThread)
    monkeypatch.setattr(config, "USE_REFINER", False)
    monkeypatch.setattr(app, "transcribe", lambda audio: "hi")
    monkeypatch.setattr(app, "paste_text", lambda text: None)

    app.on_hotkey()  # start
    timer = FakeTimer.instances[-1]

    app.on_hotkey()  # manual stop -> process synchronously
    assert timer.cancelled is True
    assert rec.stop_calls == 1

    # Late timer fire after a manual stop is a no-op.
    timer.function()
    assert rec.stop_calls == 1


def test_late_timer_fire_while_processing_is_noop(monkeypatch):
    """Race: manual stop begins processing (worker still in flight); late cap fire no-ops."""
    rec = FakeRecorder(audio="AUDIO")
    monkeypatch.setattr(app, "recorder", rec)
    _capture_tray(monkeypatch)
    monkeypatch.setattr(app.threading, "Thread", NullThread)  # worker never runs

    app.on_hotkey()  # start
    timer = FakeTimer.instances[-1]

    app.on_hotkey()  # manual stop -> processing True, worker not yet run
    assert app.processing is True
    stop_before = rec.stop_calls

    timer.function()  # late cap fire
    assert app.processing is True
    assert rec.stop_calls == stop_before


def test_transcribe_error_clears_processing_and_shows_tooltip(monkeypatch):
    rec = FakeRecorder(audio="AUDIO")
    rec._recording = True
    monkeypatch.setattr(app, "recorder", rec)
    tips = _capture_tray(monkeypatch)
    monkeypatch.setattr(app.threading, "Thread", SyncThread)
    monkeypatch.setattr(config, "USE_REFINER", False)

    def boom(audio):
        raise RuntimeError("boom")

    monkeypatch.setattr(app, "transcribe", boom)
    monkeypatch.setattr(app, "paste_text", lambda text: None)

    app.on_hotkey()

    assert app.processing is False
    assert any(
        state == "idle" and tooltip and "Error" in tooltip and "boom" in tooltip
        for state, tooltip in tips
    )


def test_no_audio_captured_resets_to_idle(monkeypatch):
    rec = FakeRecorder(audio=None)
    rec._recording = True
    monkeypatch.setattr(app, "recorder", rec)
    tips = _capture_tray(monkeypatch)
    monkeypatch.setattr(app.threading, "Thread", SyncThread)
    called = {"transcribe": False}
    monkeypatch.setattr(app, "transcribe", lambda audio: called.__setitem__("transcribe", True) or "x")
    monkeypatch.setattr(app, "paste_text", lambda text: None)

    app.on_hotkey()

    assert app.processing is False
    assert called["transcribe"] is False
    assert any(state == "idle" for state, _ in tips)


# --------------------------------------------------------------------------- #
# Shutdown (B1) — shared helper + console ctrl handler
# --------------------------------------------------------------------------- #
def test_shutdown_unhooks_and_stops_tray(monkeypatch):
    unhooked = {"count": 0}
    monkeypatch.setattr(app.keyboard, "unhook_all", lambda: unhooked.__setitem__("count", unhooked["count"] + 1))
    tray = FakeTrayIcon()
    app.tray_icon = tray

    app._shutdown()

    assert unhooked["count"] == 1
    assert tray.stop_calls == 1


def test_shutdown_safe_when_no_tray(monkeypatch):
    unhooked = {"count": 0}
    monkeypatch.setattr(app.keyboard, "unhook_all", lambda: unhooked.__setitem__("count", unhooked["count"] + 1))
    app.tray_icon = None

    # Must not raise when there is no tray icon.
    app._shutdown()

    assert unhooked["count"] == 1


def test_shutdown_is_idempotent(monkeypatch):
    unhooked = {"count": 0}
    monkeypatch.setattr(app.keyboard, "unhook_all", lambda: unhooked.__setitem__("count", unhooked["count"] + 1))
    tray = FakeTrayIcon()
    app.tray_icon = tray

    app._shutdown()
    app._shutdown()  # second call is a no-op

    assert unhooked["count"] == 1
    assert tray.stop_calls == 1


def test_console_ctrl_handler_shuts_down_on_ctrl_c(monkeypatch):
    calls = {"shutdown": 0}
    monkeypatch.setattr(app, "_shutdown", lambda: calls.__setitem__("shutdown", calls["shutdown"] + 1))

    handled = app._console_ctrl_handler(app.win32con.CTRL_C_EVENT)

    assert handled is True
    assert calls["shutdown"] == 1


def test_console_ctrl_handler_handles_close_event(monkeypatch):
    calls = {"shutdown": 0}
    monkeypatch.setattr(app, "_shutdown", lambda: calls.__setitem__("shutdown", calls["shutdown"] + 1))

    handled = app._console_ctrl_handler(app.win32con.CTRL_CLOSE_EVENT)

    assert handled is True
    assert calls["shutdown"] == 1


def test_console_ctrl_handler_ignores_unrelated_events(monkeypatch):
    calls = {"shutdown": 0}
    monkeypatch.setattr(app, "_shutdown", lambda: calls.__setitem__("shutdown", calls["shutdown"] + 1))

    # CTRL_LOGOFF_EVENT (5) is not one we handle.
    handled = app._console_ctrl_handler(5)

    assert handled is False
    assert calls["shutdown"] == 0


# --------------------------------------------------------------------------- #
# Single instance (B2)
# --------------------------------------------------------------------------- #
def test_single_instance_acquires_when_first(monkeypatch):
    monkeypatch.setattr(app.win32event, "CreateMutex", lambda a, b, name: "HANDLE")
    monkeypatch.setattr(app.win32api, "GetLastError", lambda: 0)

    assert app._acquire_single_instance() is True
    assert app._single_instance_mutex == "HANDLE"


def test_single_instance_rejects_when_already_running(monkeypatch):
    monkeypatch.setattr(app.win32event, "CreateMutex", lambda a, b, name: "HANDLE")
    monkeypatch.setattr(app.win32api, "GetLastError", lambda: app.winerror.ERROR_ALREADY_EXISTS)

    assert app._acquire_single_instance() is False
