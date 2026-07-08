"""Detect Windows resume from sleep and trigger a callback."""

import threading
import win32gui

WM_POWERBROADCAST = 0x0218
PBT_APMRESUMEAUTOMATIC = 0x0012
PBT_APMRESUMESUSPEND = 0x0007


class PowerMonitor:
    def __init__(self, on_resume, debounce_seconds=25.0):
        self._on_resume = on_resume
        self._debounce = debounce_seconds
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_POWERBROADCAST and wparam in (PBT_APMRESUMEAUTOMATIC, PBT_APMRESUMESUSPEND):
            threading.Timer(self._debounce, self._on_resume).start()
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def _run(self):
        wc = win32gui.WNDCLASS()
        wc.lpfnWndProc = self._wnd_proc
        wc.lpszClassName = "WhisperPastePowerMonitor"
        win32gui.RegisterClass(wc)
        win32gui.CreateWindow(wc.lpszClassName, "", 0, 0, 0, 0, 0, 0, 0, 0, None)
        win32gui.PumpMessages()
