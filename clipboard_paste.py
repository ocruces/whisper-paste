"""Output transcribed text — either by direct typing or via clipboard paste."""

import logging
import time

import keyboard

import clipboard_win

logger = logging.getLogger(__name__)


def _type_text(text: str):
    """Deliver text by typing it character-by-character (no clipboard involvement)."""
    time.sleep(0.1)  # let focus settle after the hotkey release
    keyboard.write(text, delay=0.04)


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
        except Exception:
            logger.warning("Clipboard snapshot failed, pasting anyway", exc_info=True)

        try:
            clipboard_win.set_text(text)
        except Exception:
            # set_text may have already emptied the clipboard before failing, so
            # try to put the user's data back, then fall back to typing so the
            # dictation is never silently dropped.
            logger.warning(
                "Clipboard set_text failed, restoring and typing instead", exc_info=True
            )
            if snap is not None:
                try:
                    clipboard_win.restore(snap)
                except Exception:
                    logger.warning("Clipboard restore failed", exc_info=True)
            _type_text(text)
            return

        time.sleep(0.1)  # let focus settle after the hotkey release
        keyboard.send("ctrl+v")
        time.sleep(config.CLIPBOARD_RESTORE_DELAY)  # let the target app read it

        if snap is not None:
            try:
                clipboard_win.restore(snap)
            except Exception:
                logger.warning("Clipboard restore failed", exc_info=True)
    else:
        _type_text(text)
