# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Run / Develop

Windows-only project. No linter or build step — the app is a Python script run from a venv. There is a pytest suite under `tests/`.

```bash
# Activate venv (Git Bash)
source .venv/Scripts/activate

# Run (faster-whisper on CPU, no LLM refinement)
python app.py

# Useful flag combos while developing
python app.py --lang en                    # FORCE language en (no auto-detect)
python app.py --model distil-small.en      # pick a different whisper model
python app.py --gpu                        # whisper.cpp w/ Vulkan (AMD GPU)
python app.py --refine                     # requires `ollama serve` running locally
python app.py --type                       # type char-by-char instead of clipboard paste

# Tests (deps in requirements-dev.txt)
pip install -r requirements-dev.txt
python -m pytest
```

The `--gpu` path needs `pywhispercpp` rebuilt with `GGML_VULKAN=1` — the PyPI wheel is CPU-only (see README §"GPU Support").

## Architecture

### Runtime model: state machine driven by a global hotkey

`app.py` is the only entry point. It owns three pieces of mutable state:
- `recorder` — a `Recorder` whose `is_recording` flag distinguishes "armed" from "recording"
- `processing` — module-level guard that ignores hotkey presses during transcription
- `tray_icon` — a `pystray.Icon` whose color (green/red/blue) mirrors the state above

The flow is: hotkey toggles `recorder` between idle→recording, then on the second press flips `processing=True` and spawns a daemon thread (`process_recording`) so transcription/refinement doesn't block the tray's main loop. The thread is responsible for clearing `processing` and resetting the tray to idle in its `finally` block. Anything that adds work between recording-stop and paste belongs in `process_recording`, not `on_hotkey`. `process_recording` gets the audio as a numpy array straight from `recorder.stop()` and passes it to `transcribe()` — there is no temp WAV file on disk.

`_state_lock` (a `threading.Lock`) guards every idle→recording→processing transition, so the hotkey press and the auto-stop timer can't both flip the state at once. It is held only for the quick state flip — the long work runs in the worker thread with the lock released. A `threading.Timer` (`_record_timer`, armed by `_start_record_timer`) force-stops a recording after `config.MAX_RECORD_SECONDS` (120s) via `_begin_processing`, the shared transition used by both the manual stop and the timeout. At startup `main()` also enforces single-instance via a named Windows mutex (`_acquire_single_instance`, exits with code 1 if another instance holds it) and registers a console ctrl handler (`_console_ctrl_handler` via `win32api.SetConsoleCtrlHandler`) so Ctrl+C / console-close run the shared `_shutdown()` (unhook hotkey + stop tray) instead of failing to unwind pystray's native message pump; `_shutdown()` is idempotent and used by both the Quit menu item and the console handler.

Separately, `main()` spawns `_preload_model` on a daemon thread at startup: it calls `transcriber.preload()` (load + warm-up transcription) so the first real dictation isn't slow, and updates the tray title to reflect load progress / errors.

### Config module is mutated at startup

`config.py` holds defaults as module-level globals (`USE_GPU`, `USE_REFINER`, `USE_CLIPBOARD`, `WHISPER_LANGUAGE`). `main()` parses CLI flags and **writes** to those attributes before any other module reads them. Other modules then `import config` and read attributes at call time (not at import time) — that's why e.g. `clipboard_paste.py` does `import config` inside the function. Don't replace this with `from config import X` at module top, or CLI flags will silently stop working.

### Transcription backend is chosen lazily

`transcriber.py` keeps `_model` and `_backend` as module globals, populated by `_get_model()` (guarded by `_model_lock` so a hotkey press and the startup preload thread can't both load it) based on `config.USE_GPU`. Two different libraries (`faster_whisper` vs `pywhispercpp`) with different APIs are abstracted behind one `transcribe(audio) -> str` that takes a 1-D float32 numpy array (16kHz mono). The faster-whisper branch passes `vad_filter=True` and `beam_size=config.BEAM_SIZE`; the whisper.cpp branch only forwards `language`. `preload()` loads the model and runs a warm-up transcription on a zero array (warm-up failures are swallowed). Heavy imports happen inside the branches so users on the CPU path never import the GPU library and vice versa.

### Sleep/resume recovery (Windows-specific)

`power_monitor.py` runs a hidden Win32 window on a daemon thread that listens for `WM_POWERBROADCAST` resume events, then calls `on_resume` after a 25-second debounce. The debounce exists because the `keyboard` library's low-level hook and the tray icon both silently die when Windows suspends, but re-registering them too early (before the input subsystem is back up) fails. `on_resume` in `app.py` toggles `tray_icon.visible` to force a redraw and re-registers the global hotkey via `_register_hotkey()`. If you change hotkey registration, mirror the change in both `_register_hotkey` and `on_resume`.

### Output: clipboard paste (default) vs typing

`clipboard_paste.paste_text` branches on `config.USE_CLIPBOARD` (True by default; `--type` sets it False). The default path snapshots the user's clipboard via `clipboard_win.snapshot()`, sets the transcript with `clipboard_win.set_text()`, sends `ctrl+v`, then restores the snapshot with `clipboard_win.restore()`. The `--type` path uses `keyboard.write` (slow, character-by-character, no clipboard involvement). The 100ms `time.sleep` before output is deliberate — it gives focus time to settle after the hotkey release before keystrokes are injected; `config.CLIPBOARD_RESTORE_DELAY` (0.3s) then gives the target app time to read the clipboard before the restore.

`clipboard_win.py` wraps `win32clipboard`. It stamps every clip it writes with the `"Clipboard Viewer Ignore"` and `"ExcludeClipboardContentFromMonitorProcessing"` registered formats so clipboard managers (Ditto) and Win+V history skip both the transcript and the restore. `snapshot`/`restore` cover CF_UNICODETEXT, CF_DIB (images) and CF_HDROP (copied files) — app-private formats are not captured. Note the handle-vs-buffer gotcha documented in that file: pywin32's `SetClipboardData` wants an integer `GlobalAlloc` handle for CF_HDROP and the two ignore formats, and a raw buffer for CF_UNICODETEXT/CF_DIB — mixing these up corrupts the heap. There is no `pyperclip` dependency anymore.
