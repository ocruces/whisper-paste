"""Tests for clipboard_paste — clipboard_win and keyboard are faked.

Verifies the paste orchestration (order of operations, sleeps, fallback,
failure tolerance) without touching the real clipboard or sending keystrokes.
"""

import importlib
import sys
import types

import pytest

import whisper_paste
from whisper_paste import config


@pytest.fixture
def cp(monkeypatch):
    events = []

    fake_cw = types.ModuleType("clipboard_win")

    def snapshot():
        events.append(("snapshot",))
        return {"snap": 1}

    def set_text(t):
        events.append(("set_text", t))

    def restore(s):
        events.append(("restore", s))

    fake_cw.snapshot = snapshot
    fake_cw.set_text = set_text
    fake_cw.restore = restore

    fake_kb = types.ModuleType("keyboard")

    def send(k):
        events.append(("send", k))

    def write(t, delay=None):
        events.append(("write", t, delay))

    fake_kb.send = send
    fake_kb.write = write

    # clipboard_paste does `from whisper_paste import clipboard_win`, which resolves
    # via the package attribute first — so override that attribute (not just
    # sys.modules) and re-import clipboard_paste fresh so it binds the fake.
    monkeypatch.setattr(whisper_paste, "clipboard_win", fake_cw, raising=False)
    monkeypatch.setitem(sys.modules, "whisper_paste.clipboard_win", fake_cw)
    monkeypatch.setitem(sys.modules, "keyboard", fake_kb)
    sys.modules.pop("whisper_paste.clipboard_paste", None)
    clipboard_paste = importlib.import_module("whisper_paste.clipboard_paste")

    monkeypatch.setattr(
        clipboard_paste.time, "sleep", lambda d: events.append(("sleep", d))
    )

    saved_use = config.USE_CLIPBOARD
    saved_delay = getattr(config, "CLIPBOARD_RESTORE_DELAY", None)
    yield clipboard_paste, fake_cw, fake_kb, events
    config.USE_CLIPBOARD = saved_use
    if saved_delay is not None:
        config.CLIPBOARD_RESTORE_DELAY = saved_delay
    sys.modules.pop("whisper_paste.clipboard_paste", None)


def test_default_path_order_and_delays(cp):
    clipboard_paste, fake_cw, fake_kb, events = cp
    config.USE_CLIPBOARD = True
    config.CLIPBOARD_RESTORE_DELAY = 0.3

    clipboard_paste.paste_text("hi")

    kinds = [e[0] for e in events]
    assert kinds == ["snapshot", "set_text", "sleep", "send", "sleep", "restore"]
    assert ("set_text", "hi") in events
    assert ("send", "ctrl+v") in events
    assert ("restore", {"snap": 1}) in events

    sleeps = [e[1] for e in events if e[0] == "sleep"]
    assert sleeps == [0.1, 0.3]  # focus settle, then restore delay


def test_typing_fallback_never_touches_clipboard(cp):
    clipboard_paste, fake_cw, fake_kb, events = cp
    config.USE_CLIPBOARD = False

    clipboard_paste.paste_text("hello")

    kinds = [e[0] for e in events]
    assert "snapshot" not in kinds
    assert "set_text" not in kinds
    assert "restore" not in kinds
    assert "send" not in kinds
    assert ("write", "hello", 0.04) in events


def test_snapshot_failure_still_pastes_and_clears_the_transcript(cp, monkeypatch):
    clipboard_paste, fake_cw, fake_kb, events = cp
    config.USE_CLIPBOARD = True
    config.CLIPBOARD_RESTORE_DELAY = 0.3

    def boom():
        raise RuntimeError("clipboard busy")

    monkeypatch.setattr(fake_cw, "snapshot", boom)

    clipboard_paste.paste_text("hi")

    assert ("set_text", "hi") in events
    assert ("send", "ctrl+v") in events
    # There is nothing to put back, but our transcript must not be left sitting
    # on the clipboard — an empty restore clears it.
    assert ("restore", {}) in events


def test_paste_failure_still_restores_the_clipboard(cp, monkeypatch):
    clipboard_paste, fake_cw, fake_kb, events = cp
    config.USE_CLIPBOARD = True
    config.CLIPBOARD_RESTORE_DELAY = 0.3

    def boom(k):
        raise RuntimeError("send failed")

    monkeypatch.setattr(fake_kb, "send", boom)

    # The error still reaches app.process_recording, but not before the user's
    # clipboard has been put back.
    with pytest.raises(RuntimeError):
        clipboard_paste.paste_text("hi")

    assert ("restore", {"snap": 1}) in events


def test_set_text_failure_without_snapshot_leaves_clipboard_alone(cp, monkeypatch):
    clipboard_paste, fake_cw, fake_kb, events = cp
    config.USE_CLIPBOARD = True

    def boom_snapshot():
        raise RuntimeError("clipboard busy")

    def boom_set(t):
        raise RuntimeError("clipboard busy")

    monkeypatch.setattr(fake_cw, "snapshot", boom_snapshot)
    monkeypatch.setattr(fake_cw, "set_text", boom_set)

    clipboard_paste.paste_text("hi")

    # set_text may have failed before it emptied the clipboard, so restoring an
    # empty snapshot here would destroy data we never captured.
    assert "restore" not in [e[0] for e in events]
    assert ("write", "hi", 0.04) in events


def test_restore_failure_does_not_raise(cp, monkeypatch):
    clipboard_paste, fake_cw, fake_kb, events = cp
    config.USE_CLIPBOARD = True
    config.CLIPBOARD_RESTORE_DELAY = 0.3

    def boom(s):
        raise RuntimeError("clipboard busy")

    monkeypatch.setattr(fake_cw, "restore", boom)

    # Must not propagate — paste already happened.
    clipboard_paste.paste_text("hi")

    assert ("send", "ctrl+v") in events


def test_set_text_failure_restores_and_types(cp, monkeypatch):
    clipboard_paste, fake_cw, fake_kb, events = cp
    config.USE_CLIPBOARD = True
    config.CLIPBOARD_RESTORE_DELAY = 0.3

    def boom(t):
        raise RuntimeError("clipboard busy")

    monkeypatch.setattr(fake_cw, "set_text", boom)

    # Must not propagate, and the transcript must still be delivered.
    clipboard_paste.paste_text("hi")

    kinds = [e[0] for e in events]
    # Snapshot was taken, set_text failed, snapshot restored, then typed.
    assert kinds == ["snapshot", "restore", "sleep", "write"]
    assert ("restore", {"snap": 1}) in events
    assert ("write", "hi", 0.04) in events
    # We must not have pasted with ctrl+v after set_text failed.
    assert "send" not in kinds
