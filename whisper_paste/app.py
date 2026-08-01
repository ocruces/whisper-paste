"""Voice Dictation Tool — Main application with system tray and global hotkey."""

import argparse
import io
import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler

import keyboard
import win32api
import win32con
import win32console
import win32event
import winerror
from PIL import Image, ImageDraw
from pystray import Icon, Menu, MenuItem

from whisper_paste import bundle
from whisper_paste import config
from whisper_paste import refiner
from whisper_paste import settings
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


# The three tray states, as module-level constants so the packaging script (and
# its test) can import the exact colour instead of repeating the literal.
COLOR_IDLE = (40, 180, 80, 255)         # green = idle/ready
COLOR_RECORDING = (220, 40, 40, 255)    # red = recording
COLOR_PROCESSING = (40, 120, 220, 255)  # blue = processing


def create_icon_image(color, size=64):
    """Create a simple colored circle icon.

    ``size`` exists so the packaging script can render this same artwork at
    256x256 for the exe's .ico — the exe icon and the tray icon must be one
    drawing, not two that drift apart. The geometry is therefore proportional;
    at the default size=64 it is byte-identical to the original fixed
    ``[8, 8, 56, 56]`` / ``width=2``.
    """
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    inset = size / 8
    width = max(2, size // 32)
    draw.ellipse(
        [inset, inset, size - inset, size - inset],
        fill=color, outline=(255, 255, 255, 200), width=width,
    )
    return img


ICON_IDLE = create_icon_image(COLOR_IDLE)
ICON_RECORDING = create_icon_image(COLOR_RECORDING)
ICON_PROCESSING = create_icon_image(COLOR_PROCESSING)


def _message_box(text, title="WhisperPaste"):
    """Show a modal error dialog. Never raises.

    This is the only user-visible channel that exists before the tray is up and
    when there is no console at all (``--windowed`` / ``pythonw.exe``, where
    ``sys.stdout`` and ``sys.stderr`` are both None). Every caller is already on
    an error path — losing the dialog must not also lose the exit code or the
    exception being reported — so it swallows failures the same way
    ``_set_tray_title`` does (see CLAUDE.md).
    """
    try:
        win32api.MessageBox(0, str(text), title,
                            win32con.MB_OK | win32con.MB_ICONERROR)
    except Exception:
        logger.exception("Failed to show message box")


def _has_console():
    """True when this process owns a console window.

    Used to skip SetConsoleCtrlHandler in a windowed build. The call is already
    wrapped in try/except, but without this gate it fails on *every* windowed
    launch and writes a full traceback into the log that maintainers will chase
    for nothing.
    """
    try:
        return win32console.GetConsoleWindow() != 0
    except Exception:
        return False


def _label():
    """Build the tray title prefix. Every tooltip in every state routes through
    here, so this is the one place to surface a durable-but-invisible setting.

    ``LOG_TRANSCRIPTS`` used to require ``--log-transcripts`` on every launch;
    now the settings file can turn it on permanently (see CLAUDE.md "Settings
    file"), which means a user could be dictating into a plaintext log across
    reboots with nothing on screen to say so. The suffix is plain BMP text, not
    an icon or emoji: _fit_tooltip measures in UTF-16 code units and an astral
    character costs two of the 127 available, a budget error tooltips already
    spend on unbounded exception text.
    """
    label = "Dictation+LLM" if config.USE_REFINER else "Dictation"
    if config.LOG_TRANSCRIPTS:
        label += " — logging transcripts"
    return label


def _idle_title():
    return f"{_label()} — Ready ({config.HOTKEY})"


# NOTIFYICONDATAW.szTip is a WCHAR[128] including the terminating NUL, so 127
# units are usable. pystray assigns Icon.title straight into that ctypes array,
# and these titles carry exception text of unbounded length.
_MAX_TOOLTIP = 127


def _fit_tooltip(text):
    """Clamp a tray tooltip to what NOTIFYICONDATAW.szTip can hold.

    Measured in UTF-16 code units, not code points — an astral character such as
    an emoji costs two of the 127.
    """
    units = " ".join(str(text).split()).encode("utf-16-le", "replace")
    if len(units) <= _MAX_TOOLTIP * 2:
        return units.decode("utf-16-le")
    cut = units[: (_MAX_TOOLTIP - 1) * 2]
    # Never split a surrogate pair — a lone high surrogate will not decode.
    if 0xD800 <= int.from_bytes(cut[-2:], "little") <= 0xDBFF:
        cut = cut[:-2]
    return cut.decode("utf-16-le") + "…"


def _set_tray_title(text):
    """Assign a tray title defensively. Never raises.

    The tray is cosmetic, but its callers are not: they hold ``_state_lock``, or
    sit between ``processing = True`` and the worker launch, or run inside an
    ``except`` block whose escape would reach the keyboard hook. A failed
    Shell_NotifyIcon (an explorer.exe restart, say) must not become their
    problem.
    """
    if tray_icon is None:
        return
    try:
        tray_icon.title = _fit_tooltip(text)
    except Exception:
        logger.exception("Failed to update tray title")


def update_tray(state, tooltip=None):
    """Update the tray icon appearance. Never raises (see _set_tray_title)."""
    if tray_icon is None:
        return
    if state == "idle":
        icon, title = ICON_IDLE, _idle_title()
    elif state == "recording":
        icon, title = ICON_RECORDING, f"{_label()} — Recording..."
    elif state == "processing":
        icon, title = ICON_PROCESSING, f"{_label()} — Processing..."
    else:
        return
    try:
        tray_icon.icon = icon
    except Exception:
        logger.exception("Failed to update tray icon")
    _set_tray_title(tooltip or title)


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
            except Exception:
                # Short, actionable text only: the tooltip goes into a fixed-size
                # Win32 field (see _fit_tooltip) and logger.exception above has
                # already recorded the full traceback.
                logger.exception("Failed to start recording")
                update_tray("idle", tooltip=f"{_label()} — Mic unavailable (in use?). Ready.")
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
        # Content only at DEBUG (--log-transcripts): the log file is durable and
        # would otherwise accumulate everything ever dictated in plaintext.
        logger.info("Transcript ready (%d chars).", len(raw_text))
        logger.debug("Raw transcript: %s", raw_text)

        if not raw_text:
            logger.info("No speech detected.")
            update_tray("idle")
            return

        # Refine with LLM (only if --refine was passed)
        if config.USE_REFINER:
            cleaned_text = refine(raw_text)
            logger.info("Refined transcript (%d chars).", len(cleaned_text))
            logger.debug("Refined text: %s", cleaned_text)
        else:
            cleaned_text = raw_text

        # Paste at cursor
        paste_text(cleaned_text)
        logger.info("Text pasted.")
        update_tray("idle")

    except Exception as e:
        logger.exception("Error during processing")
        update_tray("idle", tooltip=f"{_label()} — Error: {e} (Ready)")
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


def _resolve_log_dir():
    """Where the rotating log lives: config.LOG_DIR, else a private per-user dir.

    Defaulting outside the repository is deliberate — a clone under a shared
    path (e.g. C:\\data) inherits that location's ACL, and one under Documents or
    Desktop gets swept into OneDrive folder backup. %LOCALAPPDATA% is neither.
    No explicit DACL is set: that directory already inherits owner-only
    permissions.
    """
    if config.LOG_DIR:
        return os.path.abspath(os.path.expandvars(os.path.expanduser(config.LOG_DIR)))
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "WhisperPaste", "logs")


def _setup_logging():
    """Console + rotating file logging in a private per-user directory."""
    log_dir = _resolve_log_dir()
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "whisper-paste.log")

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(
        log_path, maxBytes=500_000, backupCount=2, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    # The rotating file is the real log either way, so it is always added.
    root.addHandler(file_handler)

    # No console handler in a windowed process (--windowed / pythonw.exe), where
    # sys.stderr is None. Adding one anyway would not crash and would not warn:
    # StreamHandler(stream=None) binds self.stream = None, emit() then raises
    # AttributeError, and Handler.handleError checks `if raiseExceptions and
    # sys.stderr:` — sees None and returns silently. Every record would be
    # discarded without a trace inside an exception handler, which is strictly
    # worse than failing loudly. So we do not create the handler at all.
    if sys.stderr is not None:
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        root.addHandler(console)

    # Transcript content is logged at DEBUG. Raise only our own logger, never
    # the root one — that would turn on debug output for faster_whisper,
    # urllib3 and friends as well.
    logging.getLogger("whisper-paste").setLevel(
        logging.DEBUG if config.LOG_TRANSCRIPTS else logging.NOTSET
    )
    logger.info("Logging to %s", log_path)


def _preload_model():
    """Load the whisper model in the background so the first dictation is fast."""
    global _startup_title
    try:
        transcriber.preload()
        _startup_title = _idle_title()
        logger.info("Model preloaded and ready.")
    except Exception as e:
        _startup_title = _fit_tooltip(f"{_label()} — Model load error: {e}")
        logger.exception("Model preload failed (will retry on first use)")

    if config.USE_REFINER:
        # Surface a missing Ollama server / unpulled model now instead of
        # silently pasting raw transcripts at the first dictation. This only
        # detects misconfiguration — anything listening on the port can claim
        # to be Ollama, which is why refiner.refine validates the response.
        try:
            ok, message = refiner.probe()
        except Exception:
            logger.exception("Ollama check failed")
        else:
            if ok:
                logger.info("Ollama check: %s", message)
            else:
                logger.warning("Ollama check: %s", message)
    # The tray may not have been created yet when this runs; _set_tray_title
    # guards for that, and main() re-syncs the title after creating the icon.
    _set_tray_title(_startup_title)


def _build_parser():
    # Every store_true flag carries default=None, not argparse's implicit False.
    # That is what makes "the user did not pass --refine" distinguishable from
    # "the user passed --refine and it is False" — without it, _apply_args would
    # write False over a `refine = true` coming from the settings file and the
    # documented precedence (defaults < ini < CLI) would silently invert.
    parser = argparse.ArgumentParser(description="Voice Dictation Tool")
    parser.add_argument(
        "--config", type=str, default=None, metavar="PATH",
        help=f"Path to a {settings.CONFIG_FILENAME} settings file. Default: the "
             f"one next to the app, else "
             f"%%LOCALAPPDATA%%\\WhisperPaste\\{settings.CONFIG_FILENAME}.",
    )
    parser.add_argument(
        "--refine", action="store_true", default=None,
        help="Enable Ollama/Gemma text refinement (uses more memory)",
    )
    parser.add_argument(
        "--gpu", action="store_true", default=None,
        help="Use whisper.cpp with GPU/Vulkan instead of faster-whisper on CPU "
             "(works with AMD GPUs). Source installs only — not available in "
             "the portable build.",
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
        "--type", action="store_true", default=None,
        help="Type the text character-by-character instead of pasting via the clipboard.",
    )
    parser.add_argument(
        "--log-dir", type=str, default=None,
        help="Directory for the rotating log file "
             "(default: %%LOCALAPPDATA%%\\WhisperPaste\\logs).",
    )
    parser.add_argument(
        "--log-transcripts", action="store_true", default=None,
        help="Also write dictated text to the log. Off by default: the log is "
             "durable, so this keeps a permanent plaintext record of everything "
             "you dictate.",
    )
    return parser


def _apply_args(args):
    """Write parsed CLI flags into `config` before any other module reads it.

    Every assignment is conditional on the flag actually having been supplied
    (None = absent, see _build_parser). Anything the user did not pass is left
    exactly as the settings file — applied just before this — left it, which is
    what implements defaults < ini < CLI.
    """
    if args.refine is not None:
        config.USE_REFINER = args.refine
    if args.gpu is not None:
        config.USE_GPU = args.gpu
    if args.type is not None:
        config.USE_CLIPBOARD = not args.type
    if args.log_transcripts is not None:
        config.LOG_TRANSCRIPTS = args.log_transcripts
    if args.log_dir:
        config.LOG_DIR = args.log_dir
    if args.lang:
        config.WHISPER_LANGUAGE = args.lang
    if args.model:
        config.WHISPER_MODEL = args.model


def _configure(argv=None):
    """Parse the command line, apply the settings file, then apply CLI flags.

    The order *is* the precedence rule (built-in defaults < ini < CLI), so keep
    these three calls together and in this sequence. Returns the
    `settings.SettingsResult` for `_report_settings` to surface once logging
    exists — it cannot be logged here, because `--log-dir`/`--log-transcripts`
    may themselves have come out of the file we just read.
    """
    args = _parse_args(argv)
    result = settings.load_and_apply(args.config)
    _apply_args(args)
    return result


def _report_settings(result):
    """Log what the settings file did, and show errors the user must see.

    Warnings (a mistyped key, a bad boolean) are log-only: the app is running
    with sane defaults and a modal dialog on every launch would be worse than
    the typo. A whole file that could not be read or parsed *is* worth a dialog
    in a frozen build, where the log is the only other channel and the user has
    no console to notice anything went wrong.
    """
    # `and not result.error`: parse_file stamps `path` before it knows the file
    # is usable, so a file that could not be parsed arrives here with both set.
    # Announcing it as loaded and then reporting it as discarded on the next
    # line reads like two different files were involved. The error text already
    # names the path.
    if result.path and not result.error:
        logger.info("Settings loaded from %s", result.path)
    for warning in result.warnings:
        logger.warning("Settings: %s", warning)
    if result.error:
        logger.error("Settings: %s", result.error)
        if bundle.is_frozen():
            _message_box(result.error)


def _parse_args(argv=None):
    """Parse the command line, surviving a windowed process with no streams.

    Under ``--windowed`` / ``pythonw.exe`` both streams are None. argparse does
    not crash on that — ``_print_message`` wraps ``file.write(message)`` in
    ``except (AttributeError, OSError): pass`` — so ``--help`` and every bad
    flag exit with the correct status and **no output whatsoever**. A silent
    exit code 2 is indistinguishable from the app simply not starting, which is
    the worst possible thing to hand a user who double-clicked an exe. When
    either stream is missing we point both at a StringIO, let argparse write
    into it, and show the result in a message box before re-raising SystemExit.

    The redirection is scoped to this one call on purpose: a process-wide
    StringIO stand-in would keep growing for the life of the app, since nothing
    ever drains it.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return _build_parser().parse_args(argv)

    buffer = io.StringIO()
    real_stdout, real_stderr = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = buffer
    try:
        return _build_parser().parse_args(argv)
    except SystemExit:
        _message_box(buffer.getvalue().strip() or "Invalid command line.")
        raise
    finally:
        sys.stdout, sys.stderr = real_stdout, real_stderr


def main():
    global tray_icon, _startup_title

    settings_result = _configure()

    _setup_logging()
    _report_settings(settings_result)

    # Reject --gpu in a frozen build. transcriber.py holds the authoritative
    # check; this one exists so the *user* sees why, because a windowed build
    # has no console to print to. Deliberately not in _apply_args: --gpu must
    # keep working on a source install, and config.USE_GPU must still reflect
    # the flag there (tests/test_cli.py pins that).
    if config.USE_GPU and bundle.is_frozen():
        logger.error("%s", bundle.GPU_UNSUPPORTED_MESSAGE)
        _message_box(bundle.GPU_UNSUPPORTED_MESSAGE)
        sys.exit(2)

    # Refuse to start a second instance — two would both register the global
    # hotkey and race on the clipboard, corrupting each other's pastes.
    if not _acquire_single_instance():
        logger.error("WhisperPaste is already running — exiting.")
        # In the portable build there is no console, so a silent exit looks
        # like a double-click that simply did nothing — the #1 support question.
        if bundle.is_frozen():
            _message_box(
                "WhisperPaste is already running.\n\n"
                "Look for the coloured circle in the system tray (you may need "
                "to expand the hidden-icons area) and right-click it to quit."
            )
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
    _set_tray_title(_startup_title)

    # Ctrl+C / console-close cannot cleanly unwind the native message pump, so
    # route those events through our own clean shutdown instead. Skipped with no
    # console (windowed build): there are no console control events to receive,
    # and the call would otherwise fail on every launch and log a traceback that
    # looks like a real fault.
    if _has_console():
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
