# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Run / Develop

Windows-only project. There is no test suite, linter, or build step — the app is a single Python script run from a venv.

```bash
# Activate venv (Git Bash)
source .venv/Scripts/activate

# Run (faster-whisper on CPU, no LLM refinement)
python app.py

# Useful flag combos while developing
python app.py --lang en              # skip language auto-detect
python app.py --gpu                  # whisper.cpp w/ Vulkan (AMD GPU)
python app.py --refine               # requires `ollama serve` running locally
python app.py --clipboard            # paste via Ctrl+V instead of typing
```

The `--gpu` path needs `pywhispercpp` rebuilt with `GGML_VULKAN=1` — the PyPI wheel is CPU-only (see README §"GPU Support").

## Architecture

### Runtime model: state machine driven by a global hotkey

`app.py` is the only entry point. It owns three pieces of mutable state:
- `recorder` — a `Recorder` whose `is_recording` flag distinguishes "armed" from "recording"
- `processing` — module-level guard that ignores hotkey presses during transcription
- `tray_icon` — a `pystray.Icon` whose color (green/red/blue) mirrors the state above

The flow is: hotkey toggles `recorder` between idle→recording, then on the second press flips `processing=True` and spawns a daemon thread (`process_recording`) so transcription/refinement doesn't block the tray's main loop. The thread is responsible for clearing `processing` and resetting the tray to idle in its `finally` block. Anything that adds work between recording-stop and paste belongs in `process_recording`, not `on_hotkey`.

### Config module is mutated at startup

`config.py` holds defaults as module-level globals (`USE_GPU`, `USE_REFINER`, `USE_CLIPBOARD`, `WHISPER_LANGUAGE`). `main()` parses CLI flags and **writes** to those attributes before any other module reads them. Other modules then `import config` and read attributes at call time (not at import time) — that's why e.g. `clipboard_paste.py` does `import config` inside the function. Don't replace this with `from config import X` at module top, or CLI flags will silently stop working.

### Transcription backend is chosen lazily

`transcriber.py` keeps `_model` and `_backend` as module globals, populated on first `transcribe()` call based on `config.USE_GPU`. Two different libraries (`faster_whisper` vs `pywhispercpp`) with different APIs are abstracted behind one `transcribe(path) -> str`. The faster-whisper branch passes `vad_filter=True` and `beam_size=5`; the whisper.cpp branch only forwards `language`. Heavy imports happen inside the branches so users on the CPU path never import the GPU library and vice versa.

### Sleep/resume recovery (Windows-specific)

`power_monitor.py` runs a hidden Win32 window on a daemon thread that listens for `WM_POWERBROADCAST` resume events, then calls `on_resume` after a 25-second debounce. The debounce exists because the `keyboard` library's low-level hook and the tray icon both silently die when Windows suspends, but re-registering them too early (before the input subsystem is back up) fails. `on_resume` in `app.py` toggles `tray_icon.visible` to force a redraw and re-registers the global hotkey via `_register_hotkey()`. If you change hotkey registration, mirror the change in both `_register_hotkey` and `on_resume`.

### Output: typing vs clipboard

`clipboard_paste.paste_text` either uses `keyboard.write` (slow, character-by-character, no clipboard pollution) or `pyperclip.copy` + `keyboard.send("ctrl+v")`. The 100ms `time.sleep` before output is deliberate — it gives focus time to settle after the hotkey release before keystrokes are injected.
