"""Tests for clipboard_win — win32clipboard is replaced with an in-memory fake.

These never touch the real Windows clipboard: a FakeClipboard instance is
injected via sys.modules before clipboard_win is (re)imported.
"""

import importlib
import struct
import sys

import pytest


class FakeClipboard:
    """Minimal stand-in for the pywin32 win32clipboard module."""

    CF_UNICODETEXT = 13
    CF_DIB = 8
    CF_HDROP = 15

    def __init__(self):
        self.store = {}
        self.opened = False
        self.open_calls = 0
        self.close_calls = 0
        self.empty_calls = 0
        self.open_fail_times = 0      # fail this many OpenClipboard calls, then succeed
        self.open_always_fail = False
        self._reg = {}
        self._next = 0xC000

    def RegisterClipboardFormat(self, name):
        if name not in self._reg:
            self._reg[name] = self._next
            self._next += 1
        return self._reg[name]

    def OpenClipboard(self, *args):
        self.open_calls += 1
        if self.open_always_fail:
            raise OSError("clipboard busy")
        if self.open_fail_times > 0:
            self.open_fail_times -= 1
            raise OSError("clipboard busy")
        self.opened = True

    def CloseClipboard(self):
        self.close_calls += 1
        self.opened = False

    def EmptyClipboard(self):
        self.empty_calls += 1
        self.store.clear()

    def SetClipboardData(self, fmt, data):
        self.store[fmt] = data

    def GetClipboardData(self, fmt):
        return self.store[fmt]

    def IsClipboardFormatAvailable(self, fmt):
        return fmt in self.store


@pytest.fixture
def cw(monkeypatch):
    fake = FakeClipboard()
    monkeypatch.setitem(sys.modules, "win32clipboard", fake)
    # Force a genuinely fresh import so the module binds our fake win32clipboard.
    # `from whisper_paste import clipboard_win` would return the already-imported
    # real module via the package attribute; import_module re-executes it.
    sys.modules.pop("whisper_paste.clipboard_win", None)
    clipboard_win = importlib.import_module("whisper_paste.clipboard_win")
    # Don't actually sleep between retries.
    monkeypatch.setattr(clipboard_win.time, "sleep", lambda *a, **k: None)
    # In production, handle-only formats (HDROP + ignore) are copied into global
    # memory and set by handle; here we pass the raw bytes straight through so the
    # fake clipboard captures them and tests can inspect the byte payloads.
    monkeypatch.setattr(clipboard_win, "_global_handle", lambda data: data)
    yield clipboard_win, fake
    sys.modules.pop("whisper_paste.clipboard_win", None)


def _ignore_formats(clipboard_win):
    return (clipboard_win.CF_VIEWER_IGNORE, clipboard_win.CF_EXCLUDE_MONITOR)


def test_set_text_empties_and_sets_text_plus_both_ignore_formats(cw):
    clipboard_win, fake = cw
    fake.store[999] = "leftover"

    clipboard_win.set_text("hello")

    assert fake.empty_calls >= 1
    assert 999 not in fake.store  # emptied first
    assert fake.store[fake.CF_UNICODETEXT] == "hello"
    for fmt in _ignore_formats(clipboard_win):
        assert fmt in fake.store
    assert fake.opened is False       # closed via finally
    assert fake.close_calls >= 1


def test_snapshot_restore_text_roundtrip_sets_ignore_formats(cw):
    clipboard_win, fake = cw
    fake.store[fake.CF_UNICODETEXT] = "original"

    snap = clipboard_win.snapshot()
    assert snap == {fake.CF_UNICODETEXT: "original"}

    clipboard_win.set_text("transcript")
    assert fake.store[fake.CF_UNICODETEXT] == "transcript"

    clipboard_win.restore(snap)
    assert fake.store[fake.CF_UNICODETEXT] == "original"
    for fmt in _ignore_formats(clipboard_win):
        assert fmt in fake.store


def test_snapshot_restore_dib_roundtrip(cw):
    clipboard_win, fake = cw
    dib = b"\x00\x01\x02\x03fake-image-bytes"
    fake.store[fake.CF_DIB] = dib

    snap = clipboard_win.snapshot()
    assert snap == {fake.CF_DIB: dib}

    fake.store.clear()
    clipboard_win.restore(snap)
    assert fake.store[fake.CF_DIB] == dib
    for fmt in _ignore_formats(clipboard_win):
        assert fmt in fake.store


def test_snapshot_restore_hdrop_builds_dropfiles(cw):
    clipboard_win, fake = cw
    paths = ("C:\\a.txt", "C:\\folder\\b.txt")
    fake.store[fake.CF_HDROP] = paths

    snap = clipboard_win.snapshot()
    assert snap[fake.CF_HDROP] == paths

    fake.store.clear()
    clipboard_win.restore(snap)

    data = fake.store[fake.CF_HDROP]
    assert isinstance(data, (bytes, bytearray))

    p_files, x, y, f_nc, f_wide = struct.unpack("<Iiiii", data[:20])
    assert p_files == 20   # header size / offset to file list
    assert x == 0 and y == 0
    assert f_nc == 0
    assert f_wide == 1     # UTF-16 path list

    payload = bytes(data[20:])
    expected = ("C:\\a.txt\0C:\\folder\\b.txt\0\0").encode("utf-16-le")
    assert payload == expected


def test_open_retries_then_succeeds(cw):
    clipboard_win, fake = cw
    fake.store[fake.CF_UNICODETEXT] = "x"
    fake.open_fail_times = 3

    snap = clipboard_win.snapshot()

    assert snap == {fake.CF_UNICODETEXT: "x"}
    assert fake.open_calls == 4  # 3 failures + 1 success


def test_open_always_failing_raises(cw):
    clipboard_win, fake = cw
    fake.open_always_fail = True

    with pytest.raises(Exception):
        clipboard_win.snapshot()

    # Even on failure the clipboard is never left open.
    assert fake.opened is False


def test_restore_empty_snapshot_clears_transcript(cw):
    clipboard_win, fake = cw
    fake.store[fake.CF_UNICODETEXT] = "transcript"

    clipboard_win.restore({})

    assert fake.empty_calls >= 1
    assert fake.CF_UNICODETEXT not in fake.store  # transcript must not linger
    for fmt in _ignore_formats(clipboard_win):
        assert fmt in fake.store


def test_snapshot_empty_clipboard_returns_empty_dict(cw):
    clipboard_win, fake = cw
    snap = clipboard_win.snapshot()
    assert snap == {}
