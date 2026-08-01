"""Tests for the app.py state machine — heavy collaborators are monkeypatched.

`import app` is import-side-effect-light (keyboard/pystray/PIL import cleanly and
no hotkey/tray is started at import time), so we import it directly and replace
its collaborators (`transcribe`, `paste_text`, `refine`, `recorder`,
`update_tray`) plus `threading.Thread`/`threading.Timer` per test.
"""

import ctypes

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
    # _register_hotkey rewrites config.HOTKEY to whatever it managed to
    # register, so the hotkey tests below mutate global state. Restoring here
    # rather than at the end of each test matters: a failing assertion would
    # skip an in-test restore and leak a bad (or None) hotkey into every later
    # test in the session.
    saved_hotkey = config.HOTKEY
    saved_hotkey_handle = app._hotkey_handle
    saved_hotkey_error = app._hotkey_error
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
    config.HOTKEY = saved_hotkey
    app._hotkey_handle = saved_hotkey_handle
    app._hotkey_error = saved_hotkey_error


class FakeTrayIcon:
    """Records stop() calls; its title setter enforces the real szTip limit.

    pystray writes Icon.title into NOTIFYICONDATAW.szTip, a WCHAR[128] ctypes
    array, so an over-long title raises out of the assignment. Reproducing that
    against the real ctypes array rather than a length check of our own keeps
    the test honest about what Windows actually accepts.
    """

    def __init__(self):
        self.stop_calls = 0
        self.icon = None
        self._title = None

    @property
    def title(self):
        return self._title

    @title.setter
    def title(self, value):
        (ctypes.c_wchar * 128)().value = value
        self._title = value

    def stop(self):
        self.stop_calls += 1


class ExplodingTrayIcon:
    """Tray whose every update fails — e.g. Shell_NotifyIcon after an explorer restart."""

    def __init__(self):
        self.stop_calls = 0

    @property
    def icon(self):
        return None

    @icon.setter
    def icon(self, value):
        raise RuntimeError("Shell_NotifyIcon failed")

    @property
    def title(self):
        return None

    @title.setter
    def title(self, value):
        raise RuntimeError("Shell_NotifyIcon failed")

    def stop(self):
        self.stop_calls += 1


def _title_units(text):
    """Length of a tray title in UTF-16 code units — the unit szTip counts in."""
    return len(text.encode("utf-16-le")) // 2


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
        state == "idle" and tooltip and "Mic unavailable" in tooltip
        for state, tooltip in tips
    )

    # Next press works normally once the mic recovers.
    rec.start_error = None
    app.on_hotkey()
    assert rec.is_recording is True
    assert rec.start_calls == 2


# Verbatim from the 2026-07-27 log. Wrapped in a tooltip this overflowed
# NOTIFYICONDATAW.szTip (137 UTF-16 units against a 128-unit array).
PORTAUDIO_ERROR = (
    "Error opening InputStream: Unanticipated host error [PaErrorCode -9999]: "
    "'Undefined external error.' [MME error 1]"
)


def test_hotkey_survives_a_long_mic_error(monkeypatch):
    """A mic error too long for szTip must not escape on_hotkey.

    Regression: the ValueError raised by the tooltip assignment replaced the
    original error and escaped into keyboard's low-level hook, which caught it
    and called CallNextHookEx anyway — defeating suppress=True and leaking the
    raw hotkey into the focused app. Uses the real update_tray on purpose.
    """
    rec = FakeRecorder(start_error=RuntimeError(PORTAUDIO_ERROR))
    monkeypatch.setattr(app, "recorder", rec)
    app.tray_icon = FakeTrayIcon()

    app.on_hotkey()

    assert app.processing is False
    assert rec.is_recording is False


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


# --------------------------------------------------------------------------- #
# Startup probe of the Ollama endpoint
# --------------------------------------------------------------------------- #
def _stub_preload(monkeypatch, probe_result):
    """Neutralise the model load and record whether the Ollama probe ran."""
    probes = []

    def fake_probe():
        probes.append(True)
        return probe_result

    monkeypatch.setattr(app.transcriber, "preload", lambda: None)
    monkeypatch.setattr(app.refiner, "probe", fake_probe)
    return probes


def test_preload_probes_ollama_when_refinement_is_enabled(monkeypatch):
    probes = _stub_preload(monkeypatch, (True, "Ollama 0.6.0 ready"))
    config.USE_REFINER = True

    app._preload_model()

    assert probes == [True]


def test_preload_skips_the_probe_without_refinement(monkeypatch):
    probes = _stub_preload(monkeypatch, (True, "Ollama 0.6.0 ready"))
    config.USE_REFINER = False

    app._preload_model()

    assert probes == []


def test_failed_probe_does_not_break_startup(monkeypatch):
    _stub_preload(monkeypatch, (False, "Ollama not reachable"))
    config.USE_REFINER = True

    # Must not raise: a missing Ollama degrades to raw transcripts, it is not fatal.
    app._preload_model()


def test_probe_exception_does_not_break_startup(monkeypatch):
    monkeypatch.setattr(app.transcriber, "preload", lambda: None)

    def boom():
        raise RuntimeError("probe blew up")

    monkeypatch.setattr(app.refiner, "probe", boom)
    config.USE_REFINER = True

    app._preload_model()


# --------------------------------------------------------------------------- #
# Tray tooltips — NOTIFYICONDATAW.szTip is a WCHAR[128]
#
# These exercise the real update_tray (not the _capture_tray stub): the whole
# point is what reaches the tray icon's title setter.
# --------------------------------------------------------------------------- #
def test_update_tray_clamps_an_over_long_tooltip():
    """Any tooltip, whatever its source, must fit szTip."""
    tray = FakeTrayIcon()
    app.tray_icon = tray

    app.update_tray("idle", tooltip="x" * 200)

    assert _title_units(tray.title) <= 127


def test_fit_tooltip_measures_utf16_units_not_code_points():
    """szTip counts UTF-16 units, so astral characters cost two apiece.

    100 emoji are only 100 code points but 200 units — a code-point-based clamp
    lets them straight through into a 128-unit array.
    """
    fitted = app._fit_tooltip("\U0001F600" * 100)

    # The real contract: this is the assignment pystray performs.
    (ctypes.c_wchar * 128)().value = fitted
    # And truncating mid-pair would leave an unencodable lone surrogate.
    assert fitted.encode("utf-16-le").decode("utf-16-le") == fitted


def test_fit_tooltip_collapses_whitespace():
    """Exception messages routinely span lines; szTip renders them badly."""
    assert app._fit_tooltip("Error: failed\n  at line 2\n") == "Error: failed at line 2"


@pytest.mark.parametrize("size", [0, 126, 127, 128, 129, 500])
def test_fit_tooltip_leaves_room_for_the_terminator(size):
    """Guard on the off-by-one: ctypes takes 128 units, Win32 wants a NUL too.

    128 would satisfy the ctypes assignment and still hand Shell_NotifyIcon an
    unterminated szTip, so the cap is 127 — easy to 'tidy' back to 128.
    """
    fitted = app._fit_tooltip("x" * size)

    assert _title_units(fitted) <= 127
    assert _title_units(fitted) == min(size, 127)


def test_begin_processing_survives_a_failing_tray(monkeypatch):
    """A cosmetic tray failure must not wedge the state machine.

    _begin_processing sets processing = True before touching the tray, so an
    exception there would strand it True forever and every later hotkey press
    would be silently ignored. Shell_NotifyIcon can fail for reasons other than
    length — an explorer.exe restart, for one.
    """
    rec = FakeRecorder(audio="AUDIO")
    rec._recording = True
    monkeypatch.setattr(app, "recorder", rec)
    monkeypatch.setattr(app.threading, "Thread", NullThread)
    app.tray_icon = ExplodingTrayIcon()
    app._start_record_timer()
    timer = FakeTimer.instances[-1]

    app._begin_processing()

    # The transition completed: worker handed off and the safety-cap cancelled.
    assert timer.cancelled is True
    assert app.processing is True  # NullThread never runs, so it stays in flight


def test_update_tray_survives_a_failing_tray():
    """update_tray itself never raises — callers hold _state_lock."""
    app.tray_icon = ExplodingTrayIcon()

    app.update_tray("idle")


def test_idle_tooltip_honours_refiner_and_hotkey(monkeypatch):
    """The idle title is built from config, not from a frozen string."""
    tray = FakeTrayIcon()
    app.tray_icon = tray
    monkeypatch.setattr(config, "USE_REFINER", True)
    monkeypatch.setattr(config, "HOTKEY", "ctrl+alt+d")
    monkeypatch.setattr(config, "LOG_TRANSCRIPTS", False)

    app.update_tray("idle")

    assert tray.title == "Dictation+LLM — Ready (ctrl+alt+d)"


@pytest.mark.parametrize("state", ["idle", "recording", "processing"])
def test_tooltip_flags_transcript_logging_when_on(monkeypatch, state):
    """LOG_TRANSCRIPTS is durable now (settings file, not just --log-transcripts),
    so every state's tooltip must say so — nothing else on screen would.
    """
    tray = FakeTrayIcon()
    app.tray_icon = tray
    monkeypatch.setattr(config, "USE_REFINER", False)
    monkeypatch.setattr(config, "LOG_TRANSCRIPTS", True)

    app.update_tray(state)

    assert "logging transcripts" in tray.title


@pytest.mark.parametrize("state", ["idle", "recording", "processing"])
def test_tooltip_omits_logging_marker_when_off(monkeypatch, state):
    tray = FakeTrayIcon()
    app.tray_icon = tray
    monkeypatch.setattr(config, "USE_REFINER", False)
    monkeypatch.setattr(config, "LOG_TRANSCRIPTS", False)

    app.update_tray(state)

    assert "logging transcripts" not in tray.title


def test_preload_survives_a_long_model_error(monkeypatch):
    """A long model-load error must not kill the preload thread.

    It would otherwise die on the title assignment, stranding the tray on
    "Loading model…" with no indication that anything went wrong.
    """
    tray = FakeTrayIcon()
    app.tray_icon = tray
    monkeypatch.setattr(config, "USE_REFINER", False)

    def boom():
        raise RuntimeError("could not load model: " + "detail " * 40)

    monkeypatch.setattr(app.transcriber, "preload", boom)

    app._preload_model()

    assert _title_units(app._startup_title) <= 127
    assert "Model load error" in tray.title


def test_preload_survives_a_long_model_error_with_logging_marker(monkeypatch):
    """Same regression as above, but with the logging-transcripts suffix in
    play — it eats into the same 127-unit budget as the exception text, so the
    clamp has to hold with both present at once.
    """
    tray = FakeTrayIcon()
    app.tray_icon = tray
    monkeypatch.setattr(config, "USE_REFINER", False)
    monkeypatch.setattr(config, "LOG_TRANSCRIPTS", True)

    def boom():
        raise RuntimeError("could not load model: " + "detail " * 40)

    monkeypatch.setattr(app.transcriber, "preload", boom)

    app._preload_model()

    assert _title_units(app._startup_title) <= 127


def test_processing_error_tooltip_is_clamped(monkeypatch):
    """Regression guard for the worker path.

    Not a driver — the clamp in update_tray already covers this. It pins the
    behaviour so a later edit cannot reintroduce the crash here, where it would
    kill the worker thread and strand the tray on the processing icon.
    """
    rec = FakeRecorder(audio="AUDIO")
    rec._recording = True
    monkeypatch.setattr(app, "recorder", rec)
    monkeypatch.setattr(app.threading, "Thread", SyncThread)
    monkeypatch.setattr(config, "USE_REFINER", False)
    monkeypatch.setattr(app, "paste_text", lambda text: None)
    tray = FakeTrayIcon()
    app.tray_icon = tray

    def boom(audio):
        raise RuntimeError("transcription failed: " + "detail " * 40)

    monkeypatch.setattr(app, "transcribe", boom)

    app.on_hotkey()

    assert app.processing is False
    assert _title_units(tray.title) <= 127


# --------------------------------------------------------------------------- #
# Hotkey registration (_register_hotkey)
#
# config.HOTKEY, app._hotkey_handle and app._hotkey_error are all restored by
# the autouse reset_app_state fixture, so no test here unwinds its own state —
# a failing assertion would skip an in-test restore and poison the session.
# --------------------------------------------------------------------------- #
def _record_registrations(monkeypatch):
    """Stand in for keyboard.add_hotkey, returning the combos it was given."""
    registered = []

    def fake_add_hotkey(hotkey, callback, **kwargs):
        registered.append(hotkey)
        return "HANDLE"

    monkeypatch.setattr(app.keyboard, "add_hotkey", fake_add_hotkey)
    return registered


def _always_fail(monkeypatch):
    def fake_add_hotkey(hotkey, callback, **kwargs):
        raise RuntimeError("add_hotkey failed")

    monkeypatch.setattr(app.keyboard, "add_hotkey", fake_add_hotkey)


def test_a_valid_hotkey_is_registered_unchanged(monkeypatch):
    config.HOTKEY = "ctrl+alt+d"
    registered = _record_registrations(monkeypatch)

    app._register_hotkey()

    assert registered == ["ctrl+alt+d"]
    assert config.HOTKEY == "ctrl+alt+d"
    assert app._hotkey_error is None


def test_a_padded_hotkey_is_stripped_without_being_called_a_fallback(monkeypatch):
    """Padding is cleaned up silently, not reported as a failure.

    keyboard.parse_hotkey rejects surrounding spaces, and neither an ini value
    nor a quoted flag arrives stripped — so stripping is routine. Setting
    _hotkey_error here would put a message box in front of a frozen user over a
    trailing space.
    """
    config.HOTKEY = "  ctrl+alt+d  "
    registered = _record_registrations(monkeypatch)

    app._register_hotkey()

    assert registered == ["ctrl+alt+d"]
    assert config.HOTKEY == "ctrl+alt+d"
    assert app._hotkey_error is None


def test_an_unparseable_hotkey_falls_back_to_the_default(monkeypatch):
    config.HOTKEY = "ctrl+shift+zzzz"
    registered = _record_registrations(monkeypatch)

    app._register_hotkey()

    assert registered == [config.DEFAULT_HOTKEY]
    assert config.HOTKEY == config.DEFAULT_HOTKEY
    assert app._hotkey_error


def test_registration_failing_on_every_candidate_leaves_no_hotkey(monkeypatch):
    """Both candidates failing must still return normally.

    _register_hotkey runs from main() before the tray icon exists, in a process
    that may have no console, and from on_resume() on the power-monitor daemon
    thread. Raising in either place is invisible to the user.
    """
    config.HOTKEY = "ctrl+alt+d"
    _always_fail(monkeypatch)

    app._register_hotkey()

    assert config.HOTKEY is None
    assert app._hotkey_handle is None
    assert app._hotkey_error


def test_idle_title_says_so_when_there_is_no_hotkey():
    config.HOTKEY = None

    title = app._idle_title()

    assert _title_units(title) <= app._MAX_TOOLTIP
    assert "None" not in title
    assert "usable hotkey" in title.lower()


def test_idle_title_is_clamped_for_a_long_user_supplied_hotkey():
    """A hotkey long enough to overflow szTip on its own must be clamped.

    keyboard accepts comma-separated *sequences*, so a parseable hotkey has no
    practical length limit — which is why _idle_title has to go through
    _fit_tooltip now that config.HOTKEY comes from the ini or --hotkey.
    """
    config.HOTKEY = ", ".join("abcdefghijklmnopqrstuvwxyz" * 8)
    assert _title_units(config.HOTKEY) > app._MAX_TOOLTIP

    title = app._idle_title()

    assert _title_units(title) <= app._MAX_TOOLTIP


# --------------------------------------------------------------------------- #
# Windows key is rejected by _validate_hotkey
#
# Windows handles win-key combinations above the low-level keyboard hook, so
# such a hotkey "registers" successfully and then silently never fires — the
# worst possible outcome. _validate_hotkey rejects them up front so the
# existing fallback machinery in _register_hotkey takes over instead.
# --------------------------------------------------------------------------- #
def test_a_windows_key_combo_is_rejected():
    assert app._validate_hotkey("win+alt+d") is None


def test_a_non_windows_combo_is_still_accepted():
    """The Windows-key guard must not over-reach onto ordinary combos."""
    assert app._validate_hotkey("ctrl+alt+d") == "ctrl+alt+d"


@pytest.mark.parametrize(
    "hotkey",
    [
        "windows+d",
        "cmd+d",
        "left windows+d",
        "right windows+d",
        "win",
    ],
)
def test_every_windows_key_spelling_is_rejected(hotkey):
    assert app._validate_hotkey(hotkey) is None


def test_a_windows_key_in_a_later_sequence_step_is_still_rejected():
    """A naive "check only the first step" implementation would miss this.

    keyboard.parse_hotkey("a, win+b") puts the Windows key in the SECOND step,
    not the first, so the whole parsed structure must be checked.
    """
    assert app._validate_hotkey("a, win+b") is None


def test_a_windows_key_hotkey_falls_back_to_the_default(monkeypatch):
    """win+alt+d "registers" but never fires, so it must fall back like an
    unparseable hotkey does — modeled on
    test_an_unparseable_hotkey_falls_back_to_the_default.
    """
    config.HOTKEY = "win+alt+d"
    registered = _record_registrations(monkeypatch)

    app._register_hotkey()

    assert registered == [config.DEFAULT_HOTKEY]
    assert config.HOTKEY == config.DEFAULT_HOTKEY
    assert app._hotkey_error
