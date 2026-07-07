"""Windows clipboard snapshot/restore that stays invisible to clipboard managers.

Wraps win32clipboard to (a) put the transcript on the clipboard stamped with the
"Clipboard Viewer Ignore" and "ExcludeClipboardContentFromMonitorProcessing"
formats — so Ditto and Windows clipboard history (Win+V) skip it — and (b)
capture and restore whatever the user already had (text, images, copied files),
also stamped ignore so restoring doesn't create a duplicate history entry.

Note on SetClipboardData: pywin32 only accepts a Python str/bytes buffer for a
handful of special-cased formats (CF_UNICODETEXT, CF_DIB, ...). For every other
format — including CF_HDROP and our two registered ignore formats — it expects
an integer handle to global memory, so we allocate one via GlobalAlloc. Passing
bytes for those formats corrupts the heap and crashes the process.
"""

import ctypes
from ctypes import wintypes
import struct
import time

import win32clipboard

# Custom formats that tell clipboard managers to leave a clip alone.
CF_VIEWER_IGNORE = win32clipboard.RegisterClipboardFormat("Clipboard Viewer Ignore")
CF_EXCLUDE_MONITOR = win32clipboard.RegisterClipboardFormat(
    "ExcludeClipboardContentFromMonitorProcessing"
)

# Standard formats we can faithfully snapshot and restore.
CF_UNICODETEXT = win32clipboard.CF_UNICODETEXT
CF_DIB = win32clipboard.CF_DIB
CF_HDROP = win32clipboard.CF_HDROP

_SNAPSHOT_FORMATS = (CF_UNICODETEXT, CF_DIB, CF_HDROP)

# Formats pywin32 won't take a raw buffer for — we must hand it a global handle.
_HANDLE_FORMATS = frozenset((CF_HDROP, CF_VIEWER_IGNORE, CF_EXCLUDE_MONITOR))

_OPEN_ATTEMPTS = 10
_OPEN_RETRY_DELAY = 0.05  # seconds between OpenClipboard retries

_GMEM_MOVEABLE = 0x0002
_kernel32 = ctypes.windll.kernel32
_kernel32.GlobalAlloc.restype = ctypes.c_void_p
_kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
_kernel32.GlobalLock.restype = ctypes.c_void_p
_kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
_kernel32.GlobalUnlock.restype = wintypes.BOOL
_kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
_kernel32.GlobalFree.restype = ctypes.c_void_p
_kernel32.GlobalFree.argtypes = [ctypes.c_void_p]


def _global_handle(data):
    """Copy `data` (bytes) into GMEM_MOVEABLE global memory and return the handle.

    Ownership passes to the clipboard once SetClipboardData succeeds, so we don't
    free it here.
    """
    size = len(data)
    handle = _kernel32.GlobalAlloc(_GMEM_MOVEABLE, size)
    if not handle:
        raise MemoryError("GlobalAlloc failed for clipboard data")
    ptr = _kernel32.GlobalLock(handle)
    if not ptr:
        _kernel32.GlobalFree(handle)
        raise MemoryError("GlobalLock failed for clipboard data")
    try:
        ctypes.memmove(ptr, data, size)
    finally:
        _kernel32.GlobalUnlock(handle)
    return handle


def _open_clipboard():
    """Open the clipboard, retrying briefly if another app holds it.

    Raises the last error if it never opens.
    """
    last_error = None
    for _ in range(_OPEN_ATTEMPTS):
        try:
            win32clipboard.OpenClipboard()
            return
        except Exception as exc:  # pywintypes.error when the clipboard is busy
            last_error = exc
            time.sleep(_OPEN_RETRY_DELAY)
    raise last_error


def _set(fmt, data):
    """SetClipboardData, allocating a global handle for handle-only formats."""
    if fmt in _HANDLE_FORMATS:
        win32clipboard.SetClipboardData(fmt, _global_handle(data))
    else:
        win32clipboard.SetClipboardData(fmt, data)


def _mark_ignored():
    """Stamp the two ignore formats onto the currently open clipboard."""
    _set(CF_VIEWER_IGNORE, b"1")
    _set(CF_EXCLUDE_MONITOR, b"1")


def _build_dropfiles(paths):
    """Build a CF_HDROP payload (DROPFILES header + UTF-16 path list) for `paths`."""
    # DROPFILES: DWORD pFiles; POINT pt; BOOL fNC; BOOL fWide  (20 bytes)
    header = struct.pack("<Iiiii", 20, 0, 0, 0, 1)  # pFiles=20, pt=(0,0), fNC=0, fWide=1
    path_list = ("\0".join(paths) + "\0\0").encode("utf-16-le")
    return header + path_list


def snapshot():
    """Capture the current clipboard for the formats we can restore.

    Returns a dict {format_id: data}. Empty clipboard -> empty dict.
    """
    _open_clipboard()
    try:
        snap = {}
        for fmt in _SNAPSHOT_FORMATS:
            if win32clipboard.IsClipboardFormatAvailable(fmt):
                snap[fmt] = win32clipboard.GetClipboardData(fmt)
        return snap
    finally:
        win32clipboard.CloseClipboard()


def set_text(text):
    """Replace the clipboard with `text`, stamped so clipboard managers skip it."""
    _open_clipboard()
    try:
        win32clipboard.EmptyClipboard()
        _set(CF_UNICODETEXT, text)
        _mark_ignored()
    finally:
        win32clipboard.CloseClipboard()


def restore(snap):
    """Write the captured formats back, stamped ignore so no duplicate is logged.

    An empty snapshot just clears our transcript off the clipboard.
    """
    _open_clipboard()
    try:
        win32clipboard.EmptyClipboard()
        for fmt, data in snap.items():
            if fmt == CF_HDROP:
                _set(CF_HDROP, _build_dropfiles(data))
            else:
                _set(fmt, data)
        _mark_ignored()
    finally:
        win32clipboard.CloseClipboard()
