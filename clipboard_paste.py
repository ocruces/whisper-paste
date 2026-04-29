"""Output transcribed text — either by direct typing or via clipboard paste."""

import time
import keyboard


def paste_text(text: str):
    """Output text at the current cursor position."""
    import config

    if config.USE_CLIPBOARD:
        import pyperclip
        pyperclip.copy(text)
        time.sleep(0.1)
        keyboard.send("ctrl+v")
    else:
        time.sleep(0.1)
        keyboard.write(text, delay=0.02)
