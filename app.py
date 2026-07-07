"""Voice Dictation Tool — Main application with system tray and global hotkey."""

import argparse
import threading
import keyboard
from PIL import Image, ImageDraw
from pystray import Icon, Menu, MenuItem

import config
from recorder import Recorder
from transcriber import transcribe
from refiner import refine
from clipboard_paste import paste_text

recorder = Recorder()
tray_icon: Icon = None
processing = False
_hotkey_handle = None


def create_icon_image(color):
    """Create a simple colored circle icon."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([8, 8, 56, 56], fill=color, outline=(255, 255, 255, 200), width=2)
    return img


ICON_IDLE = create_icon_image((40, 180, 80, 255))         # green = idle/ready
ICON_RECORDING = create_icon_image((220, 40, 40, 255))    # red = recording
ICON_PROCESSING = create_icon_image((40, 120, 220, 255))  # blue = processing


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


def on_hotkey():
    """Handle the global hotkey press."""
    global processing

    if processing:
        return  # ignore while processing a previous recording

    if not recorder.is_recording:
        # Start recording
        recorder.start()
        update_tray("recording")
        print("Recording started...")
    else:
        # Stop recording and process
        processing = True
        update_tray("processing")
        print("Recording stopped. Processing...")

        # Run transcription + refinement in a thread to avoid blocking
        threading.Thread(target=process_recording, daemon=True).start()


def process_recording():
    """Transcribe, refine, and paste the recorded audio."""
    global processing
    try:
        audio = recorder.stop()
        if audio is None:
            print("No audio captured.")
            return

        # Transcribe
        raw_text = transcribe(audio)
        print(f"Raw transcript: {raw_text}")

        if not raw_text:
            print("No speech detected.")
            return

        # Refine with LLM (only if --refine was passed)
        if config.USE_REFINER:
            cleaned_text = refine(raw_text)
            print(f"Refined text: {cleaned_text}")
        else:
            cleaned_text = raw_text

        # Paste at cursor
        paste_text(cleaned_text)
        print("Text pasted.")

    except Exception as e:
        print(f"Error during processing: {e}")
    finally:
        processing = False
        update_tray("idle")


def on_quit(icon, item):
    """Quit the application."""
    keyboard.unhook_all()
    icon.stop()


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
    except Exception as e:
        print(f"Failed to refresh tray icon: {e}")
    try:
        if _hotkey_handle is not None:
            keyboard.remove_hotkey(_hotkey_handle)
    except (KeyError, ValueError):
        pass
    _register_hotkey()
    print("Resumed — tray icon and hotkey re-registered.")


def main():
    global tray_icon

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
        "--clipboard", action="store_true",
        help="Use clipboard (Ctrl+V) instead of direct typing. Faster but leaves text in clipboard history.",
    )
    args = parser.parse_args()

    config.USE_REFINER = args.refine
    config.USE_GPU = args.gpu
    config.USE_CLIPBOARD = args.clipboard
    if args.lang:
        config.WHISPER_LANGUAGE = args.lang

    engine = "whisper.cpp (GPU/Vulkan)" if config.USE_GPU else "faster-whisper (CPU)"
    refine_mode = " + Ollama refinement" if config.USE_REFINER else ""
    lang_info = f", language: {config.WHISPER_LANGUAGE}" if config.WHISPER_LANGUAGE else ", language: auto-detect"
    print("Voice Dictation Tool")
    print(f"Engine: {engine}{refine_mode}{lang_info}")
    print(f"Hotkey: {config.HOTKEY}")
    print("Press the hotkey to start recording, press again to stop and paste.")
    print("The app runs in the system tray. Right-click the tray icon to quit.")

    # Register global hotkey
    _register_hotkey()

    # Create and run system tray icon
    label = "Dictation+LLM" if config.USE_REFINER else "Dictation"
    tray_icon = Icon(
        "dictation",
        ICON_IDLE,
        title=f"{label} — Ready ({config.HOTKEY})",
        menu=Menu(MenuItem("Quit", on_quit)),
    )
    from power_monitor import PowerMonitor
    PowerMonitor(on_resume=on_resume)
    tray_icon.run()


if __name__ == "__main__":
    main()
