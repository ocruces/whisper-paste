# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Run / Develop

Windows-only project. The app is the `whisper_paste` package, run from a venv. No
linter step; there is a pytest suite under `tests/`. Run it as a module (`python -m
whisper_paste`) or via the `whisper-paste` console script installed by `pip install -e .`.

```bash
# Activate venv (Git Bash)
source .venv/Scripts/activate

# Run (faster-whisper on CPU, no LLM refinement)
python -m whisper_paste

# Useful flag combos while developing
python -m whisper_paste --lang en                    # FORCE language en (no auto-detect)
python -m whisper_paste --model distil-small.en      # pick a different whisper model
python -m whisper_paste --gpu                        # whisper.cpp w/ Vulkan (AMD GPU)
python -m whisper_paste --refine                     # requires `ollama serve` running locally
python -m whisper_paste --type                       # type char-by-char instead of clipboard paste

# Tests (deps in requirements-dev.txt)
pip install -r requirements-dev.txt
python -m pytest
```

The `--gpu` path needs `pywhispercpp` rebuilt with `GGML_VULKAN=1` — the PyPI wheel is CPU-only (see README §"GPU Support").

## Architecture

### Runtime model: state machine driven by a global hotkey

`whisper_paste/app.py` holds the entry point (`main`), launched via `python -m
whisper_paste` — through `whisper_paste/__main__.py` — or the `whisper-paste` console
script. It owns three pieces of mutable state:
- `recorder` — a `Recorder` whose `is_recording` flag distinguishes "armed" from "recording"
- `processing` — module-level guard that ignores hotkey presses during transcription
- `tray_icon` — a `pystray.Icon` whose color (green/red/blue) mirrors the state above

The flow is: hotkey toggles `recorder` between idle→recording, then on the second press flips `processing=True` and spawns a daemon thread (`process_recording`) so transcription/refinement doesn't block the tray's main loop. The thread is responsible for clearing `processing` and resetting the tray to idle in its `finally` block. Anything that adds work between recording-stop and paste belongs in `process_recording`, not `on_hotkey`. `process_recording` gets the audio as a numpy array straight from `recorder.stop()` and passes it to `transcribe()` — there is no temp WAV file on disk.

`_state_lock` (a `threading.Lock`) guards every idle→recording→processing transition, so the hotkey press and the auto-stop timer can't both flip the state at once. It is held only for the quick state flip — the long work runs in the worker thread with the lock released. A `threading.Timer` (`_record_timer`, armed by `_start_record_timer`) force-stops a recording after `config.MAX_RECORD_SECONDS` (120s) via `_begin_processing`, the shared transition used by both the manual stop and the timeout. At startup `main()` also enforces single-instance via a named Windows mutex (`_acquire_single_instance`, exits with code 1 if another instance holds it) and registers a console ctrl handler (`_console_ctrl_handler` via `win32api.SetConsoleCtrlHandler`) so Ctrl+C / console-close run the shared `_shutdown()` (unhook hotkey + stop tray) instead of failing to unwind pystray's native message pump; `_shutdown()` is idempotent and used by both the Quit menu item and the console handler.

Separately, `main()` spawns `_preload_model` on a daemon thread at startup: it calls `transcriber.preload()` (load + warm-up transcription) so the first real dictation isn't slow, and updates the tray title to reflect load progress / errors.

### Tray titles are a fixed-size Win32 field

`pystray` writes `Icon.title` into `NOTIFYICONDATAW.szTip`, a `WCHAR[128]` ctypes array, so an over-long title raises `ValueError` out of the *assignment*. Several tray titles interpolate exception text of unbounded length, which is why every one of them goes through `_fit_tooltip` — it clamps to `_MAX_TOOLTIP` (127, leaving room for the terminating NUL) measured in **UTF-16 code units, not code points**, and collapses whitespace. Don't raise that cap to 128: ctypes accepts 128 but then hands Shell_NotifyIcon an unterminated string.

`update_tray` and `_set_tray_title` additionally **never raise**. That is load-bearing, not defensive habit: `_begin_processing` sets `processing = True` before it touches the tray, so an exception there would strand the flag `True` and silently ignore every later hotkey press; and `on_hotkey`'s mic-failure handler runs inside keyboard's low-level hook, where an escaping exception makes the library fall through to `CallNextHookEx` — defeating `suppress=True` and leaking the raw hotkey into the focused app. That combination is what produced the 2026-07-27 crash. Keep error tooltips short and put the detail in `logger.exception` instead.

### Config module is mutated at startup

`config.py` holds defaults as module-level globals (`USE_GPU`, `USE_REFINER`, `USE_CLIPBOARD`, `WHISPER_LANGUAGE`, `LOG_DIR`, `LOG_TRANSCRIPTS`). `main()` calls `_apply_args(_build_parser().parse_args())`, which **writes** to those attributes before any other module reads them (the parser and the apply step are split out so `tests/test_cli.py` can exercise flag→config wiring without starting the tray). Other modules then `from whisper_paste import config` (a module reference) and read attributes at call time (not at import time) — that's why e.g. `clipboard_paste.py` does `from whisper_paste import config` inside the function. Don't replace this with `from whisper_paste.config import USE_GPU` (binding the value) at module top, or CLI flags will silently stop working. Immutable settings that are never CLI-mutated (e.g. `SAMPLE_RATE`, `OLLAMA_URL`) are still value-imported directly, which is fine.

### Transcription backend is chosen lazily

`transcriber.py` keeps `_model` and `_backend` as module globals, populated by `_get_model()` (guarded by `_model_lock` so a hotkey press and the startup preload thread can't both load it) based on `config.USE_GPU`. Two different libraries (`faster_whisper` vs `pywhispercpp`) with different APIs are abstracted behind one `transcribe(audio) -> str` that takes a 1-D float32 numpy array (16kHz mono). The faster-whisper branch passes `vad_filter=True` and `beam_size=config.BEAM_SIZE`; the whisper.cpp branch only forwards `language`. `preload()` loads the model and runs a warm-up transcription on a zero array (warm-up failures are swallowed). Heavy imports happen inside the branches so users on the CPU path never import the GPU library and vice versa.

### Refiner output is untrusted input

Ollama's API is unauthenticated and whatever answers on `OLLAMA_URL` gets its reply pasted at the user's cursor, so `refiner.refine` validates the response before returning it: non-`str` bodies, over-long replies (`MAX_EXPANSION_FACTOR`/`MAX_EXPANSION_CHARS`), C0/C1 control characters and invisible bidi/zero-width characters all fail closed to the raw transcript. `\t` and `\n` are deliberately **allowed** — multi-line formatting is the whole point of the refiner, and `config.REFINER_PROMPT` rule 8 constrains the model to plain text plus `- `/`1. ` markers, which is what makes that character policy sufficient. Don't "tidy" the validator into rejecting newlines. Editing `REFINER_PROMPT` has one hard constraint: no literal `{`/`}` except the `{text}` placeholder, or `.format()` raises on the refine path only (`tests/test_refiner.py` guards this).

`refiner.probe()` runs from `_preload_model` when `config.USE_REFINER` and reports server version + whether `OLLAMA_MODEL` is pulled. It is a misconfiguration detector, not authentication — anything on the port can return a version string. Never word its messages as if it proves the peer's identity.

### Logging

`_resolve_log_dir()` puts the rotating log in `%LOCALAPPDATA%\WhisperPaste\logs` unless `--log-dir` overrides it — deliberately outside the repo, so the log's ACL doesn't depend on the clone location and OneDrive folder backup never picks it up. Transcript text is logged at DEBUG only, enabled by `--log-transcripts`, which raises the level of the `whisper-paste` logger alone (raising root would turn on debug for `faster_whisper`/`urllib3` too). INFO lines carry character counts, never content — keep it that way when adding logging to the transcription path.

### Sleep/resume recovery (Windows-specific)

`power_monitor.py` runs a hidden Win32 window on a daemon thread that listens for `WM_POWERBROADCAST` resume events, then calls `on_resume` after a 25-second debounce. The debounce exists because the `keyboard` library's low-level hook and the tray icon both silently die when Windows suspends, but re-registering them too early (before the input subsystem is back up) fails. `on_resume` in `app.py` toggles `tray_icon.visible` to force a redraw and re-registers the global hotkey via `_register_hotkey()`. If you change hotkey registration, mirror the change in both `_register_hotkey` and `on_resume`.

### Output: clipboard paste (default) vs typing

`clipboard_paste.paste_text` branches on `config.USE_CLIPBOARD` (True by default; `--type` sets it False). The default path snapshots the user's clipboard via `clipboard_win.snapshot()`, sets the transcript with `clipboard_win.set_text()`, sends `ctrl+v`, then restores the snapshot with `clipboard_win.restore()`. The restore is in a `finally` and **always** runs once `set_text` has succeeded — when the snapshot itself failed it restores `{}`, which clears the transcript rather than leaving it on the clipboard for any process to read. The `set_text`-failure branch is the deliberate exception: it restores only a snapshot that actually succeeded, because `set_text` may have failed before emptying the clipboard and an empty restore would then destroy data that was never captured. The `--type` path uses `keyboard.write` (slow, character-by-character, no clipboard involvement). The 100ms `time.sleep` before output is deliberate — it gives focus time to settle after the hotkey release before keystrokes are injected; `config.CLIPBOARD_RESTORE_DELAY` (0.3s) then gives the target app time to read the clipboard before the restore.

`clipboard_win.py` wraps `win32clipboard`. It stamps every clip it writes with the `"Clipboard Viewer Ignore"` and `"ExcludeClipboardContentFromMonitorProcessing"` registered formats so clipboard managers (Ditto) and Win+V history skip both the transcript and the restore. `snapshot`/`restore` cover CF_UNICODETEXT, CF_DIB (images) and CF_HDROP (copied files) — app-private formats are not captured. Note the handle-vs-buffer gotcha documented in that file: pywin32's `SetClipboardData` wants an integer `GlobalAlloc` handle for CF_HDROP and the two ignore formats, and a raw buffer for CF_UNICODETEXT/CF_DIB — mixing these up corrupts the heap. There is no `pyperclip` dependency anymore.
