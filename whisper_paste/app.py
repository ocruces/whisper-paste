"""Voice Dictation Tool — Main application with system tray and global hotkey."""

import argparse
import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler

import keyboard
import win32api
import win32con
import win32event
import winerror
from PIL import Image, ImageDraw
from pystray import Icon, Menu, MenuItem

from whisper_paste import config
from whisper_paste import transcriber
from whisper_paste.recorder import Recorder
from whisper_paste.transcriber import transcribe
from whisper_paste.refiner import refine
from whisper_paste.clipboard_paste import paste_text

logger = logging.getLogger("whisper-paste")

recorder = Recorder()
tray_icon: Icon = None
processing = False
_hotkey_handle = None

# Guards every idle -> recording -> processing transition. It is held only for
# the quick state flip (start the mic / arm the worker); the long work
# (transcription, paste) runs in the worker thread with the lock released.
_state_lock = threading.Lock()

# Safety-cap timer that force-stops a recording after config.MAX_RECORD_SECONDS.
_record_timer = None

# Tray title reflecting model-load progress; updated by the preload thread.
_startup_title = "Dictation — Loading model…"

# Held for the whole process lifetime so the single-instance named mutex is not
# released early (a released mutex would let a second launch slip through).
_single_instance_mutex = None

# Guards _shutdown() so the Quit menu item and the console ctrl handler (which
# runs on a separate OS thread) cannot each tear the tray down twice.
_shutting_down = False


def create_icon_image(color):
    """Create a simple colored circle icon."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([8, 8, 56, 56], fill=color, outline=(255, 255, 255, 200), width=2)
    return img


ICON_IDLE = create_icon_image((40, 180, 80, 255))         # green = idle/ready
ICON_RECORDING = create_icon_image((220, 40, 40, 255))    # red = recording
ICON_PROCESSING = create_icon_image((40, 120, 220, 255))  # blue = processing


def _label():
    return "Dictation+LLM" if config.USE_REFINER else "Dictation"


def _idle_title():
    return f"{_label()} — Ready ({config.HOTKEY})"


def update_tray(state, tooltip=None):
    """Update the tray icon appearance."""
    if tray_icon is None:
        return
    if state == "idle":
        tray_icon.icon = ICON_IDLE
        tray_icon.title = tooltip or "Dictation — Ready (Ctrl+Shift+Space)"
    elif state == "recording":
        tray_icon.icon = ICON_RECORDING
        tray_icon.title = tooltip or "Dictation — Recording..."
    elif state == "processing":
        tray_icon.icon = ICON_PROCESSING
        tray_icon.title = tooltip or "Dictation — Processing..."


def _start_record_timer():
    """Arm the auto-stop safety-cap timer (cancels any existing one first)."""
    global _record_timer
    _cancel_record_timer()
    timer = threading.Timer(config.MAX_RECORD_SECONDS, _on_record_timeout)
    timer.daemon = True
    _record_timer = timer
    timer.start()


def _cancel_record_timer():
    """Cancel and forget the auto-stop timer if one is armed."""
    global _record_timer
    if _record_timer is not None:
        _record_timer.cancel()
        _record_timer = None


def _begin_processing():
    """Flip recording -> processing and spawn the worker.

    Must be called while holding ``_state_lock`` with the recorder actually
    recording and ``processing`` still False. Shared by ``on_hotkey`` (manual
    stop) and the auto-stop timer so the transition happens exactly once.
    """
    global processing
    processing = True
    _cancel_record_timer()
    update_tray("processing")
    logger.info("Recording stopped. Processing...")
    threading.Thread(target=process_recording, daemon=True).start()


def _on_record_timeout():
    """Auto-stop callback: process the recording if it is still in progress."""
    with _state_lock:
        if recorder.is_recording and not processing:
            logger.info("Max recording time (%ss) reached — auto-stopping.",
                        config.MAX_RECORD_SECONDS)
            _begin_processing()


def on_hotkey():
    """Handle the global hotkey press."""
    with _state_lock:
        if processing:
            return  # ignore while processing a previous recording

        if not recorder.is_recording:
            # Start recording. A mic failure must leave us cleanly idle.
            try:
                recorder.start()
            except Exception as e:
                logger.exception("Failed to start recording")
                update_tray("idle", tooltip=f"Dictation — Mic error: {e}")
                return
            _start_record_timer()
            update_tray("recording")
            logger.info("Recording started...")
        else:
            _begin_processing()


def process_recording():
    """Transcribe, refine, and paste the recorded audio (runs off the lock)."""
    global processing
    try:
        audio = recorder.stop()
        if audio is None:
            logger.info("No audio captured.")
            update_tray("idle")
            return

        raw_text = transcribe(audio)
        logger.info("Raw transcript: %s", raw_text)

        if not raw_text:
            logger.info("No speech detected.")
            update_tray("idle")
            return

        # Refine with LLM (only if --refine was passed)
        if config.USE_REFINER:
            cleaned_text = refine(raw_text)
            logger.info("Refined text: %s", cleaned_text)
        else:
            cleaned_text = raw_text

        # Paste at cursor
        paste_text(cleaned_text)
        logger.info("Text pasted.")
        update_tray("idle")

    except Exception as e:
        logger.exception("Error during processing")
        update_tray("idle", tooltip=f"Dictation — Error: {e} (Ready)")
    finally:
        processing = False


def _shutdown():
    """Clean shutdown shared by the Quit menu item and the console ctrl handler.

    Idempotent (safe to call twice) and thread-safe to call from the console
    handler's OS thread — pystray's ``Icon.stop()`` may be called from any
    thread. Unhooks the global hotkey and stops the tray icon.
    """
    global _shutting_down
    if _shutting_down:
        return
    _shutting_down = True
    try:
        keyboard.unhook_all()
    except Exception:
        logger.exception("Failed to unhook keyboard during shutdown")
    if tray_icon is not None:
        try:
            tray_icon.stop()
        except Exception:
            logger.exception("Failed to stop tray icon during shutdown")


def on_quit(icon, item):
    """Quit the application (Quit menu item)."""
    _shutdown()


def _console_ctrl_handler(ctrl_type):
    """Handle console CTRL events so Ctrl+C actually terminates the app.

    KeyboardInterrupt cannot cleanly unwind pystray's native Win32 message
    pump, so we intercept the control event here and run the same clean
    shutdown as the Quit menu item. Returns True when handled so the default
    handler does not also fire.
    """
    if ctrl_type in (win32con.CTRL_C_EVENT, win32con.CTRL_BREAK_EVENT,
                     win32con.CTRL_CLOSE_EVENT):
        logger.info("Console control event %s received — shutting down.", ctrl_type)
        _shutdown()
        return True
    return False


def _acquire_single_instance():
    """Acquire the single-instance named mutex.

    Returns True if this is the only running instance, False if another
    WhisperPaste is already running. The handle is stashed on a module global
    so it lives for the whole process lifetime.
    """
    global _single_instance_mutex
    _single_instance_mutex = win32event.CreateMutex(
        None, False, "WhisperPaste_SingleInstance"
    )
    return win32api.GetLastError() != winerror.ERROR_ALREADY_EXISTS


def _register_hotkey():
    global _hotkey_handle
    _hotkey_handle = keyboard.add_hotkey(
        config.HOTKEY, on_hotkey, suppress=True, trigger_on_release=True
    )


def on_resume():
    global _hotkey_handle
    try:
        if tray_icon is not None:
            tray_icon.visible = False
            tray_icon.visible = True
    except Exception:
        logger.exception("Failed to refresh tray icon")
    try:
        if _hotkey_handle is not None:
            keyboard.remove_hotkey(_hotkey_handle)
    except (KeyError, ValueError):
        pass
    _register_hotkey()
    logger.info("Resumed — tray icon and hotkey re-registered.")


def _setup_logging():
    """Console + rotating file logging, with logs/ in the project root."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log_dir = os.path.join(project_root, config.LOG_DIR)
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "whisper-paste.log")

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    file_handler = RotatingFileHandler(
        log_path, maxBytes=500_000, backupCount=2, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(console)
    root.addHandler(file_handler)


def _preload_model():
    """Load the whisper model in the background so the first dictation is fast."""
    global _startup_title
    try:
        transcriber.preload()
        _startup_title = _idle_title()
        logger.info("Model preloaded and ready.")
    except Exception as e:
        _startup_title = f"{_label()} — Model load error: {e}"
        logger.exception("Model preload failed (will retry on first use)")
    # The tray may not have been created yet when this runs; update_tray/title
    # guards for that, and main() re-syncs the title after creating the icon.
    if tray_icon is not None:
        tray_icon.title = _startup_title


def main():
    global tray_icon, _startup_title

    parser = argparse.ArgumentParser(description="Voice Dictation Tool")
    parser.add_argument(
        "--refine", action="store_true",
        help="Enable Ollama/Gemma text refinement (uses more memory)",
    )
    parser.add_argument(
        "--gpu", action="store_true",
        help="Use whisper.cpp with GPU/Vulkan instead of faster-whisper on CPU (works with AMD GPUs)",
    )
    parser.add_argument(
        "--lang", type=str, default=None,
        help="Language code (e.g. en, es, fr). Skips auto-detection for faster results.",
    )
    parser.add_argument(
        "--model", type=str, default=None,
        help="Whisper model name, e.g. tiny, base, small, distil-small.en.",
    )
    parser.add_argument(
        "--type", action="store_true",
        help="Type the text character-by-character instead of pasting via the clipboard.",
    )
    args = parser.parse_args()

    config.USE_REFINER = args.refine
    config.USE_GPU = args.gpu
    config.USE_CLIPBOARD = not args.type
    if args.lang:
        config.WHISPER_LANGUAGE = args.lang
    if args.model:
        config.WHISPER_MODEL = args.model

    _setup_logging()

    # Refuse to start a second instance — two would both register the global
    # hotkey and race on the clipboard, corrupting each other's pastes.
    if not _acquire_single_instance():
        logger.error("WhisperPaste is already running — exiting.")
        sys.exit(1)

    engine = "whisper.cpp (GPU/Vulkan)" if config.USE_GPU else "faster-whisper (CPU)"
    refine_mode = " + Ollama refinement" if config.USE_REFINER else ""
    lang_info = (
        f", language: {config.WHISPER_LANGUAGE}"
        if config.WHISPER_LANGUAGE else ", language: auto-detect"
    )
    output_mode = "clipboard paste" if config.USE_CLIPBOARD else "character typing"
    logger.info("Voice Dictation Tool")
    logger.info("Engine: %s%s%s", engine, refine_mode, lang_info)
    logger.info("Model: %s | Output: %s", config.WHISPER_MODEL, output_mode)
    logger.info("Hotkey: %s", config.HOTKEY)
    logger.info("Press the hotkey to start recording, press again to stop and paste.")
    logger.info("The app runs in the system tray. Right-click the tray icon to quit.")

    # Preload the model in the background while the tray/hotkey come up.
    _startup_title = f"{_label()} — Loading model…"
    threading.Thread(target=_preload_model, daemon=True).start()

    # Register global hotkey
    _register_hotkey()

    # Create and run system tray icon
    tray_icon = Icon(
        "dictation",
        ICON_IDLE,
        title=_startup_title,
        menu=Menu(MenuItem("Quit", on_quit)),
    )
    # Re-sync in case preload finished while the icon was being constructed.
    tray_icon.title = _startup_title

    # Ctrl+C / console-close cannot cleanly unwind the native message pump, so
    # route those events through our own clean shutdown instead.
    try:
        win32api.SetConsoleCtrlHandler(_console_ctrl_handler, True)
    except Exception:
        logger.exception("Failed to register console control handler")

    from whisper_paste.power_monitor import PowerMonitor
    PowerMonitor(on_resume=on_resume)
    # Belt-and-braces: if a stray KeyboardInterrupt still surfaces from the
    # pump, exit cleanly via the same shutdown path.
    try:
        tray_icon.run()
    except KeyboardInterrupt:
        _shutdown()


if __name__ == "__main__":
    main()
