"""Output transcribed text — either by direct typing or via clipboard paste."""

import time

import keyboard

import clipboard_win


def paste_text(text: str):
    """Output text at the current cursor position."""
    import config

    if config.USE_CLIPBOARD:
        # Snapshot the user's clipboard so we can put it back afterwards. If the
        # clipboard is stuck we still paste — losing the restore beats losing the
        # transcript.
        snap = None
        try:
            snap = clipboard_win.snapshot()
        except Exception as exc:
            print(f"WhisperPaste: clipboard snapshot failed, pasting anyway: {exc}")

        clipboard_win.set_text(text)
        time.sleep(0.1)  # let focus settle after the hotkey release
        keyboard.send("ctrl+v")
        time.sleep(config.CLIPBOARD_RESTORE_DELAY)  # let the target app read it

        if snap is not None:
            try:
                clipboard_win.restore(snap)
            except Exception as exc:
                print(f"WhisperPaste: clipboard restore failed: {exc}")
    else:
        time.sleep(0.1)
        keyboard.write(text, delay=0.04)
