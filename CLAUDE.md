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

The portable ZIP is built by `scripts\build.ps1` (Python 3.11, its own venv, ~90s of PyInstaller plus a model download). It takes minutes, so don't run it to check a docs change — see §"Packaging: the portable ZIP".

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

`config.py` holds defaults as module-level globals (`USE_GPU`, `USE_REFINER`, `USE_CLIPBOARD`, `WHISPER_LANGUAGE`, `LOG_DIR`, `LOG_TRANSCRIPTS`). `main()` calls `_configure()`, which parses the command line, applies the settings file and then calls `_apply_args(args)` — all three **write** to those attributes before any other module reads them (the parser and the apply step are split out so `tests/test_cli.py` can exercise flag→config wiring without starting the tray; see §"Settings file" for the precedence those two writers implement). Other modules then `from whisper_paste import config` (a module reference) and read attributes at call time (not at import time) — that's why e.g. `clipboard_paste.py` does `from whisper_paste import config` inside the function. Don't replace this with `from whisper_paste.config import USE_GPU` (binding the value) at module top, or CLI flags will silently stop working. Immutable settings that are never CLI-mutated (e.g. `SAMPLE_RATE`, `OLLAMA_URL`) are still value-imported directly, which is fine.

### Settings file: defaults < ini < CLI flags

`settings.py` reads `whisper-paste.ini` (UTF-8, one `[whisper-paste]` section, seven optional keys mapping to `config` globals) because the portable build is launched by double-clicking an exe and a double-click cannot pass flags. `main()` does the whole thing through `_configure()`, and **the order of its three calls is the precedence rule**: parse the command line, `settings.load_and_apply(args.config)`, then `_apply_args(args)`. Keep them together and in that sequence.

What makes the "CLI wins" half work is that **every `store_true` flag in `_build_parser` carries `default=None`**, not argparse's implicit `False`, and every assignment in `_apply_args` is guarded by `is not None`. Without that, a bare `WhisperPaste.exe` launch would write `USE_REFINER = False` over a `refine = true` that the user had just put in the ini, and the documented precedence would silently invert for exactly the flags that have no negative form. `tests/test_settings.py` pins this — don't "simplify" those `default=None`s away. The other half is that `parse_file` returns *only* the keys a file actually contained, so `apply_settings` never touches a setting the user did not write.

The search order is: explicit `--config PATH`, then `whisper-paste.ini` beside the exe (beside the repo root from source), then `%LOCALAPPDATA%\WhisperPaste\whisper-paste.ini`; first existing file wins and no merging happens. A missing file from the search is normal and silent, but a missing **explicit** `--config` is an error — the user named that file deliberately, so falling back would run under settings they believe are in force. Note the ZIP ships a copy next to the exe, so the per-user path is only reachable if that one is removed; the shipped template says so.

**A settings file must never prevent startup.** It is read before the tray exists, in a process that usually has no console, so every failure mode degrades: a bad boolean, an unknown key or a stray section become warnings and the default is used; an unreadable or unparseable file becomes `SettingsResult.error` and the whole file is skipped. Nothing in the module logs, because `--log-dir` and `--log-transcripts` are themselves settings it may supply — logging is not configured yet. That is why it returns a `SettingsResult` that `_report_settings` surfaces *after* `_setup_logging`, warnings to the log only and `error` additionally through `_message_box` when frozen (a modal dialog per launch for a typo'd key would be worse than the typo). `configparser` is built with `interpolation=None` because `log_dir = %LOCALAPPDATA%\WhisperPaste\logs` is a legitimate, documented value that `BasicInterpolation` would reject, and `strict=False` so a duplicated key is last-one-wins rather than a discarded file. `_parse_path` deliberately leaves `log_dir` unexpanded: `_resolve_log_dir` already applies `expandvars`/`expanduser`/`abspath`, and expanding twice would mangle a literal `%` in a directory name.

### Transcription backend is chosen lazily

`transcriber.py` keeps `_model` and `_backend` as module globals, populated by `_get_model()` (guarded by `_model_lock` so a hotkey press and the startup preload thread can't both load it) based on `config.USE_GPU`. Two different libraries (`faster_whisper` vs `pywhispercpp`) with different APIs are abstracted behind one `transcribe(audio) -> str` that takes a 1-D float32 numpy array (16kHz mono). The faster-whisper branch passes `vad_filter=True` and `beam_size=config.BEAM_SIZE`; the whisper.cpp branch only forwards `language`. `preload()` loads the model and runs a warm-up transcription on a zero array (warm-up failures are swallowed). Heavy imports happen inside the branches so users on the CPU path never import the GPU library and vice versa.

### Frozen builds: what changes and what must not

`bundle.py` is the only module that knows the app can be an exe, and it answers two questions for everyone else. `is_frozen()` requires **both** `sys.frozen` and `sys._MEIPASS`: `sys.frozen` alone is set by other freezers (and py2exe-era shims) and says nothing about the on-disk layout, whereas `sys._MEIPASS` is what makes the `<exe dir>\models\<name>` layout `bundled_model_dir` assumes actually real. `bundle.py` deliberately imports no `config`, so it stays outside the import-order rules the rest of the package lives under and is testable by monkeypatching `sys` alone.

The bundled model lives at `<exe dir>\models\<config.WHISPER_MODEL>` and is resolved by `transcriber._resolve_faster_whisper_model()` **at load time inside `_get_model()`**, never at import time — same config-mutation rule as everything else (see above): `main()` rewrites `config.WHISPER_MODEL` from `--model` after `transcriber` has been imported. Handing `WhisperModel` a directory is legitimate; it does `os.path.isdir(...)` before falling back to `download_model()`, so a bundled dir short-circuits HuggingFace and an unbundled name downloads exactly as it does from source. The separator guard in `bundled_model_dir` does double duty: it lets repo ids (`Systran/faster-whisper-small`) and absolute paths through to `WhisperModel` untouched, **and** it stops `--model ..\..\x` from aiming the lookup outside the bundle. Keying on the bare name is also what makes "drop your own converted model into `models\my-model\` and pass `--model my-model`" work, which the README documents — don't narrow it to a hard-coded allow-list of model names.

`--gpu` is refused in `_get_model()` **before** the `pywhispercpp` import, and that placement is the entire point: `_get_model()` is the one chokepoint every transcription path crosses, so a `RuntimeError` there reaches the user as a tray tooltip via `_preload_model` instead of a bare `ModuleNotFoundError` from an import that could never have succeeded (the portable build excludes the package twice over). `main()` pre-empts it with `_message_box` + `sys.exit(2)` purely so a windowed user — who has no console and may not think to hover the tray — sees the reason at all. `_apply_args` still sets `config.USE_GPU` when frozen, on purpose: the source path shares that code, and `tests/test_cli.py` pins flag→config wiring. The refusal belongs at the two use sites, not in the parser.

### Refiner output is untrusted input

Ollama's API is unauthenticated and whatever answers on `OLLAMA_URL` gets its reply pasted at the user's cursor, so `refiner.refine` validates the response before returning it: non-`str` bodies, over-long replies (`MAX_EXPANSION_FACTOR`/`MAX_EXPANSION_CHARS`), C0/C1 control characters and invisible bidi/zero-width characters all fail closed to the raw transcript. `\t` and `\n` are deliberately **allowed** — multi-line formatting is the whole point of the refiner, and `config.REFINER_PROMPT` rule 8 constrains the model to plain text plus `- `/`1. ` markers, which is what makes that character policy sufficient. Don't "tidy" the validator into rejecting newlines. Editing `REFINER_PROMPT` has one hard constraint: no literal `{`/`}` except the `{text}` placeholder, or `.format()` raises on the refine path only (`tests/test_refiner.py` guards this).

`refiner.probe()` runs from `_preload_model` when `config.USE_REFINER` and reports server version + whether `OLLAMA_MODEL` is pulled. It is a misconfiguration detector, not authentication — anything on the port can return a version string. Never word its messages as if it proves the peer's identity.

### Logging

`_resolve_log_dir()` puts the rotating log in `%LOCALAPPDATA%\WhisperPaste\logs` unless `--log-dir` overrides it — deliberately outside the repo, so the log's ACL doesn't depend on the clone location and OneDrive folder backup never picks it up. Transcript text is logged at DEBUG only, enabled by `--log-transcripts`, which raises the level of the `whisper-paste` logger alone (raising root would turn on debug for `faster_whisper`/`urllib3` too). INFO lines carry character counts, never content — keep it that way when adding logging to the transcription path.

### No console: sys.stdout and sys.stderr are None

True under PyInstaller's `--windowed` build **and** under `pythonw.exe`, which `scripts\run.ps1 -NoConsole` has always used — so the handling below fixed a pre-existing bug rather than paying a packaging tax. A GUI-subsystem exe launched from an existing console does not attach to it either (measured: `sys.stdout is None`, `GetConsoleWindow() == 0`), which is why no `.cmd` wrapper can make `WhisperPaste.exe` print and why a second console-subsystem twin exists instead.

`_setup_logging` adds the `StreamHandler` **only when `sys.stderr is not None`**. Not defensiveness: `StreamHandler(stream=None)` does not raise — it binds `self.stream = None`, `emit()` then raises `AttributeError`, and `Handler.handleError` checks `if raiseExceptions and sys.stderr:`, sees None and returns. Every record would be discarded with no trace, inside an exception handler. Silent discard is worse than failing, so the handler is never created. The rotating file handler is added unconditionally and is the real log either way.

Be precise about argparse: it does **not** crash on missing streams. `_print_message` wraps `file.write(message)` in `except (AttributeError, OSError): pass`, so `--help` and every bad flag exit with the correct status and **no output whatsoever** — and a silent exit code 2 is indistinguishable, to someone who double-clicked an exe, from the app not starting. `_parse_args` therefore points both streams at a `StringIO`, lets argparse write into it, and shows the captured text in a message box before re-raising `SystemExit`. The redirection is **scoped to that one call**: a process-wide stand-in would grow for the life of the app because nothing ever drains it.

`SetConsoleCtrlHandler` is skipped when `_has_console()` is false — there are no console control events to receive, and the call would otherwise fail on every windowed launch and log a traceback that reads like a real fault. `_message_box` must never raise, for the same reason and with the same discipline as `_set_tray_title`: every caller is already on an error path, and losing the dialog must not also lose the exit code or the exception being reported.

### Sleep/resume recovery (Windows-specific)

`power_monitor.py` runs a hidden Win32 window on a daemon thread that listens for `WM_POWERBROADCAST` resume events, then calls `on_resume` after a 25-second debounce. The debounce exists because the `keyboard` library's low-level hook and the tray icon both silently die when Windows suspends, but re-registering them too early (before the input subsystem is back up) fails. `on_resume` in `app.py` toggles `tray_icon.visible` to force a redraw and re-registers the global hotkey via `_register_hotkey()`. If you change hotkey registration, mirror the change in both `_register_hotkey` and `on_resume`.

### Output: clipboard paste (default) vs typing

`clipboard_paste.paste_text` branches on `config.USE_CLIPBOARD` (True by default; `--type` sets it False). The default path snapshots the user's clipboard via `clipboard_win.snapshot()`, sets the transcript with `clipboard_win.set_text()`, sends `ctrl+v`, then restores the snapshot with `clipboard_win.restore()`. The restore is in a `finally` and **always** runs once `set_text` has succeeded — when the snapshot itself failed it restores `{}`, which clears the transcript rather than leaving it on the clipboard for any process to read. The `set_text`-failure branch is the deliberate exception: it restores only a snapshot that actually succeeded, because `set_text` may have failed before emptying the clipboard and an empty restore would then destroy data that was never captured. The `--type` path uses `keyboard.write` (slow, character-by-character, no clipboard involvement). The 100ms `time.sleep` before output is deliberate — it gives focus time to settle after the hotkey release before keystrokes are injected; `config.CLIPBOARD_RESTORE_DELAY` (0.3s) then gives the target app time to read the clipboard before the restore.

`clipboard_win.py` wraps `win32clipboard`. It stamps every clip it writes with the `"Clipboard Viewer Ignore"` and `"ExcludeClipboardContentFromMonitorProcessing"` registered formats so clipboard managers (Ditto) and Win+V history skip both the transcript and the restore. `snapshot`/`restore` cover CF_UNICODETEXT, CF_DIB (images) and CF_HDROP (copied files) — app-private formats are not captured. Note the handle-vs-buffer gotcha documented in that file: pywin32's `SetClipboardData` wants an integer `GlobalAlloc` handle for CF_HDROP and the two ignore formats, and a raw buffer for CF_UNICODETEXT/CF_DIB — mixing these up corrupts the heap. There is no `pyperclip` dependency anymore.

### Packaging: the portable ZIP

`scripts\build.ps1` is the only supported way to build the distributable, and it works in `build\venv` — never `.venv`. That is not tidiness: a contributor's `.venv` may hold a locally rebuilt `pywhispercpp` or an editable install, and either would leak into a shipped artifact. `requirements-build.txt` is fully pinned (with a `BUILD_PYTHON = 3.11` marker the script greps out, because the file pins package *versions*, not wheel tags — a different Python minor resolves different wheels for identical version strings, and PyInstaller ships whichever interpreter it ran under). `pywhispercpp` is absent from that file **and** named in the spec's `excludes`: two independent guards against the same accident, because either one alone lets the whisper.cpp runtime back into the ZIP after a venv slip. `build.ps1` then asserts it a third time by trying to import it in the build venv, and the post-build gate fails if `_internal\pywhispercpp` exists.

**The spec has exactly one mandatory data directive: `collect_data_files('faster_whisper')`.** `faster_whisper/vad.py` computes the Silero VAD path from its own `__file__`, which static analysis cannot see; without the directive there is no `faster_whisper/assets/silero_vad_v6.onnx` and the first real dictation dies with `NoSuchFile` — `transcribe()` passes `vad_filter=True` on every faster-whisper call, so that is the hot path, not an edge case. Everything else that looks necessary is already done by PyInstaller's own hooks and hooks-contrib (`onnxruntime`, `sounddevice`/PortAudio, `pystray._win32`, `av`, numpy, PIL); adding manual `collect_*` calls for those produces doubled DLLs and a second place to go stale, so **don't**. `ctranslate2` needs no runtime hook either: `importlib.resources.files()` resolves correctly under PyInstaller 6.x because the `.pyd` sits beside the DLLs in the package directory. That was verified empirically rather than reasoned about, and is worth re-verifying on a dependency bump. The one deliberate piece of insurance is `collect_submodules('huggingface_hub')` — it is a lazy-loading package whose import graph shows almost nothing, and it is reached only on the download fallback that no unit test exercises.

`av` cannot be excluded: `faster_whisper/__init__.py` imports `audio.py`, which imports it at module level, even though nothing here ever calls `decode_audio`. That is ~62 MB of FFmpeg carried for one unused import. Replacing it with a stub package is a known, deliberately deferred follow-up — and note it is also what creates the LGPL 2.1+ obligation documented in `packaging\THIRD-PARTY-NOTICES.md`.

The model is copied in **after** PyInstaller, by `build.ps1`, rather than declared in `datas`. Two reasons, both load-bearing: a 461 MB `datas` entry is hashed by `Analysis` and copied again by `COLLECT` on every build, including builds where only a `.py` changed; and `datas` land under `_internal\`, whereas the model must sit next to the exe for `bundle.bundled_model_dir` (a `datas` destination containing `..` is rejected outright). Keeping it out also makes the spec model-agnostic, so `-Model medium` needs no edit to it. `-Clean` never deletes `build\models` — re-downloading 461 MB to retest a spec change is pure waste — and the download is SHA-256-verified against `packaging\models.json` in PowerShell afterwards, deliberately not by the library that fetched it.

`upx=False` on both EXEs and on COLLECT, and it stays False even on machines without UPX installed: packed executables are a strong AV heuristic trigger and this app already looks keylogger-shaped (a `WH_KEYBOARD_LL` hook with `suppress=True`, plus clipboard writes). Two EXEs share one `Analysis`, one `PYZ` and one `COLLECT` because a windowed exe gets no console at all (see above) — the twin is genuinely the same program and costs about a megabyte, not a second copy of the payload.
