# WhisperPaste

**Voice dictation for Windows — press a global hotkey, speak, and the transcription is pasted at your cursor. 100% local, no cloud.**

![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Platform: Windows](https://img.shields.io/badge/platform-Windows%2010%2F11-lightgrey)

WhisperPaste lives in your system tray. Press `Ctrl+Shift+Space` in any application, dictate, press it again, and the transcribed text appears at the cursor. Speech recognition runs on-device with [Whisper](https://github.com/openai/whisper) — nothing is sent to a server.

```
tray icon:   🟢 idle / ready       →  🔴 recording       →  🔵 processing       →  🟢 idle
             (press hotkey)           (speak; press again)   (transcribe + paste)
```

## Features

- **Global hotkey** (`Ctrl+Shift+Space`) works in any application.
- **Local Whisper transcription** — [faster-whisper](https://github.com/SYSTRAN/faster-whisper) on CPU by default, or [whisper.cpp](https://github.com/ggerganov/whisper.cpp) with Vulkan for GPU acceleration (including AMD GPUs) via `--gpu`.
- **Instant clipboard paste with restore** — pastes with `Ctrl+V`, then puts your previous clipboard back (text, images, and copied file lists are all restored).
- **Invisible to clipboard managers** — the transcript is stamped with the `Clipboard Viewer Ignore` and `ExcludeClipboardContentFromMonitorProcessing` formats, so clipboard managers (e.g. Ditto) and the Windows `Win+V` history never record it.
- **Optional LLM refinement** — pass `--refine` to clean up grammar and coherence with a local [Ollama](https://ollama.com) model.
- **Model preloading** — the Whisper model is loaded and warmed up in the background at startup, so the first dictation isn't slow.
- **Auto-stop safety cap** — a forgotten recording is force-stopped and processed after 120 seconds.
- **Sleep/resume recovery** — the global hotkey and tray icon are automatically re-registered after Windows suspends.
- **Single-instance guard** — a second launch exits cleanly instead of racing the first on the hotkey and clipboard.
- **Rotating file logs** in a private per-user directory (`%LOCALAPPDATA%\WhisperPaste\logs`). **Dictated text is never written to them** unless you pass `--log-transcripts`.
- **Portable build** — a self-contained ZIP with the Whisper model inside: unzip, double-click, no Python, no first-run download.

## Requirements

**Portable ZIP** — no Python needed:

- Windows 10 or 11
- A microphone
- ~750 MB of free disk space

**Install from source** — additionally:

- Python 3.10 or newer

On a source install the first run downloads the Whisper model (the background preload fetches it). That needs internet **once**; after the model is cached, transcription is fully offline. The portable ZIP already contains the model, so it never does this.

## Download (portable ZIP)

Download `WhisperPaste-1.0.0-win64-small.zip` from the [Releases page](https://github.com/ocruces/whisper-paste/releases), unzip it wherever you like, and double-click **`WhisperPaste.exe`**. A green circle appears in the system tray — press `Ctrl+Shift+Space` and dictate.

- **The Whisper model is inside the ZIP, so the first run works offline.** Nothing is downloaded at launch.
- **Nothing is installed.** The folder is self-contained; the only thing written outside it is the log in `%LOCALAPPDATA%\WhisperPaste\logs`. To uninstall, quit from the tray icon and delete the folder.
- **Unzipped it is about 716 MB** — the `small` model is ~486 MB of that, the code and embedded Python runtime ~252 MB.
- **Give it about 13 seconds.** The tray icon appears at once, but its tooltip reads "Loading model…" until the model has been loaded and warmed up.

Verify the download against the SHA-256 published with the release:

```powershell
Get-FileHash .\WhisperPaste-1.0.0-win64-small.zip -Algorithm SHA256
```

Flags work exactly as they do on a source install — pass them to the exe (or add them to a shortcut's target):

```powershell
.\WhisperPaste.exe --lang en
```

### What's in the folder

| Item | What it is |
|------|------------|
| `WhisperPaste.exe` | The app. Windowed: it has no console and prints nothing anywhere — the log file is its output. |
| `WhisperPaste-debug.exe` | The identical program linked as a console application, so startup errors are readable. |
| `debug-console.cmd` | Launches `WhisperPaste-debug.exe`, forwards your flags, and keeps the window open with the exit code. |
| `WhisperPaste-en.cmd`, `WhisperPaste-es.cmd` | One-click launchers that start the app with a fixed dictation language — see [Settings and language](#settings-and-language). Which ones exist is a build option; releases ship `en` and `es`. |
| `whisper-paste.ini` | Settings file (language, model, hotkey, output mode, logging). Ships with every line commented out, so it changes nothing until you edit it. |
| `models\` | The bundled Whisper model (`small` by default). Drop your own in here too — see [Flags](#flags). |
| `_internal\` | Python runtime and libraries. Leave it alone; both exes need it. |
| `licenses\` | Licence texts harvested from the exact package versions in this build (36 packages). |
| `THIRD-PARTY-NOTICES.md` | What the bundled pystray and FFmpeg licences require, and how this build meets them. |
| `BUILD-INFO.txt` | Version, git commit, model repo and revision, Python and PyInstaller versions, and the hash of the pinned build requirements. |
| `README.md`, `LICENSE` | This file, and the MIT licence. |

### Settings and language

The portable build is normally started by double-clicking, and a double-click cannot pass flags — so anything you would otherwise type on a command line has two other homes.

**One-click language launchers.** Double-click `WhisperPaste-es.cmd` instead of `WhisperPaste.exe` to dictate in Spanish. That is all a launcher is: the same exe started with `--lang es`. Extra flags are forwarded, so `WhisperPaste-es.cmd --refine` works, and a repeated flag wins, so `WhisperPaste-es.cmd --lang fr` overrides the baked-in language for that one run. Which launchers ship is decided at build time (see [Building the portable ZIP](#building-the-portable-zip)); they are regenerated by every build, so don't hand-edit them.

**`whisper-paste.ini`, for everything else.** Open it in Notepad, delete the `;` at the start of the setting you want, change the value, save, and restart WhisperPaste — settings are read once, at startup. The keys are `language`, `model`, `hotkey`, `refine`, `output` (`clipboard` or `type`), `log_transcripts` and `log_dir`, each documented in the file next to its default. In `log_dir`, Windows shortcuts like `%LOCALAPPDATA%`, `%USERPROFILE%` and `~` are expanded. Unlike a launcher, this also applies when the app is started from a shortcut, from autostart, or by a plain double-click on the exe.

WhisperPaste reads the **first** of these that exists and then stops — never two merged:

1. the file named by `--config PATH` (if you name one that does not exist, it says so and starts with the built-in defaults rather than silently falling through),
2. `whisper-paste.ini` next to `WhisperPaste.exe`,
3. `%LOCALAPPDATA%\WhisperPaste\whisper-paste.ini` — a personal copy that survives replacing the folder with a newer release. Because the ZIP ships the copy in 2, that one wins; delete or rename it for the personal copy to take effect.

A broken settings file never stops the app: a mistyped key or a bad value is logged and the default is used, and a file that cannot be read or parsed at all is logged, shown in a dialog, and skipped.

**Precedence is built-in defaults < `whisper-paste.ini` < command-line flags.** A flag always wins over the file — including the `--lang` baked into a launcher — which is the first thing to check if a setting looks like it is being ignored.

**Quit the running copy before switching language.** Only one instance may run at a time, so double-clicking a second launcher shows an "already running" dialog instead of switching languages. Right-click the tray icon → **Quit**, then start the launcher you want.

### Windows SmartScreen and antivirus

**The executable is not code-signed.** The first launch therefore shows SmartScreen's "Windows protected your PC" dialog; choose **More info → Run anyway**. If you downloaded the ZIP with a browser it also carries the mark of the web, which the extracted files inherit — `Unblock-File .\WhisperPaste-1.0.0-win64-small.zip` before extracting avoids that.

**Some antivirus engines will flag it, and that is not surprising.** A global hotkey genuinely is a `WH_KEYBOARD_LL` low-level keyboard hook, and the app genuinely writes the clipboard and injects `Ctrl+V`. Heuristically that is the shape of a keylogger; a scanner cannot tell from the binary that the keystrokes are never recorded. Being a large unsigned PyInstaller bundle does not help either.

This README will not tell you the warnings are wrong, and nobody can promise you a clean scan. What is offered instead is evidence you can check yourself:

- **The SHA-256 published with each release** tells you that you have the file the maintainer built, not something rewritten in transit.
- **`BUILD-INFO.txt` inside the ZIP** records the git commit, the model repository and revision, the Python and PyInstaller versions, and the SHA-256 of `requirements-build.txt`.
- **`scripts\build.ps1` is the build.** It is in this repository, it installs a fully pinned dependency set into its own venv, and it involves no private tooling, key or build server — so you can rebuild the artifact yourself from source and compare.

Maintainers should publish a VirusTotal result alongside the SHA-256 with every release.

If you would rather not run an unsigned binary at all, **install from source** — every dependency then comes from PyPI in the ordinary way and nothing is a prebuilt binary from this project.

## Install from source

```powershell
git clone https://github.com/ocruces/whisper-paste.git
cd whisper-paste
scripts\install.ps1      # creates .venv and installs dependencies
scripts\run.ps1          # launches the tray app
```

You can also double-click `scripts\install.bat` and `scripts\run.bat` if you prefer not to use a PowerShell prompt.

- Contributors: `scripts\install.ps1 -Dev` also installs the test dependencies.
- Tray-only session (no console window): `scripts\run.ps1 -NoConsole` — quit it from the tray icon.
- Extra flags pass straight through: `scripts\run.ps1 --model small --lang en`.

## Usage

1. Press `Ctrl+Shift+Space` — the tray icon turns 🔴 **red** (recording).
2. Speak.
3. Press `Ctrl+Shift+Space` again — the icon turns 🔵 **blue** (processing).
4. The text is pasted at your cursor and your previous clipboard is restored — the icon returns to 🟢 **green** (ready).

If you forget the second press, recording auto-stops and is processed after 120 seconds.

**Quit:** right-click the tray icon and choose **Quit**, or press `Ctrl+C` in the console if you started it from one (the portable `WhisperPaste.exe` has no console — quit it from the tray).

### Tray icon states

| Color | State |
|-------|-------|
| 🟢 Green | Idle / ready |
| 🔴 Red | Recording |
| 🔵 Blue | Processing (transcribing / pasting) |

### Flags

Pass flags after `scripts\run.ps1` (or `python -m whisper_paste`, or `WhisperPaste.exe` in the portable build). Defaults come from `whisper_paste/config.py`.

| Flag | Effect | Default |
|------|--------|---------|
| `--model NAME` | Whisper model to load (`tiny`, `base`, `small`, `medium`, `large-v3`, `distil-small.en`, …). In the portable build the name is looked up in `models\NAME` next to the exe first — see below. | `small` |
| `--lang CODE` | **Forces** the transcription language (e.g. `en`, `es`, `fr`) — audio is transcribed as this language regardless of what is spoken, and Whisper skips its language-detection pass. | auto-detect |
| `--gpu` | Use the whisper.cpp / Vulkan backend instead of faster-whisper on CPU (see [GPU support](#gpu-support-amd--non-nvidia)). **Source install only** — the portable build refuses the flag with an error dialog and exits with code 2. | off (CPU) |
| `--refine` | Clean up the transcript with a local Ollama model (see [Text refinement](#text-refinement-optional)). | off |
| `--type` | Type the text character-by-character instead of pasting via the clipboard (slower, never touches the clipboard). | off (clipboard paste) |
| `--log-dir PATH` | Directory for the rotating log file. | `%LOCALAPPDATA%\WhisperPaste\logs` |
| `--log-transcripts` | Also write dictated text to the log. See [Privacy](#privacy). | off |
| `--config PATH` | Read settings from this `whisper-paste.ini` instead of searching for one — see [Settings and language](#settings-and-language). | next to the app, else `%LOCALAPPDATA%\WhisperPaste\whisper-paste.ini` |

**Tip: pass `--lang` if you always dictate in the same language.** With auto-detect (the default), Whisper first runs a language-detection pass over the beginning of every recording, then transcribes it. Setting `--lang en` skips that pass, so each dictation is transcribed sooner. The trade-off is that the language you set is applied to whatever you say, so leave it off only if you actually switch languages between dictations.

Flags can be combined, e.g. `scripts\run.ps1 --gpu --lang en --refine`.

**Portable build: `--model` and your own models.** In the frozen exe, a bare model name is resolved against `models\NAME` next to `WhisperPaste.exe` before anything else. The ZIP ships one model there (`small` unless the release says otherwise), which is why the first run is offline. Any other bare name — `--model medium`, say — finds nothing bundled and falls through to the usual HuggingFace download, so it needs internet once and then caches under `~/.cache/huggingface/hub/` like a source install does.

That lookup is also the supported way to use a model the release did not ship: put a CTranslate2-converted Whisper model directory (`model.bin`, `config.json`, `tokenizer.json`, `vocabulary.txt`) into `models\my-model\` next to the exe and run

```powershell
.\WhisperPaste.exe --model my-model
```

A name containing `/` or `\` is never treated as a bundled model — a HuggingFace repo id (`Systran/faster-whisper-medium`) or a full path (`D:\models\my-model`) is passed to faster-whisper untouched.

## Privacy

Audio never leaves the machine: transcription is local, and `--refine` talks only to Ollama on `localhost`.

- **Logs contain no dictated text.** The log records metadata (character counts, timings, errors), not what you said. `--log-transcripts` turns content logging on for debugging — it makes the log a permanent plaintext record of everything you dictate, so leave it off unless you need it, and delete the log afterwards.
- **The log lives outside the repository**, in `%LOCALAPPDATA%\WhisperPaste\logs`. That keeps its file permissions from depending on where you cloned the project (a clone under a shared path such as `C:\data` inherits that location's ACL) and keeps it out of OneDrive folder backup.
- **The transcript is cleared from the clipboard** after the paste, even when the pre-paste snapshot failed.
- **Refiner output is validated** before it is pasted (see below).

## How it works

The hotkey toggles recording. Audio is captured from the default microphone with [sounddevice](https://python-sounddevice.readthedocs.io/) as a 16 kHz mono float32 numpy array (no temporary WAV file), handed to Whisper for transcription, optionally refined, and then delivered at the cursor. Everything runs in a small state machine driven from the tray icon; the transcription/paste work happens on a worker thread so the tray stays responsive.

**Clipboard invisibility.** In the default paste mode, WhisperPaste snapshots your current clipboard, writes the transcript (stamped with the `Clipboard Viewer Ignore` and `ExcludeClipboardContentFromMonitorProcessing` formats), sends `Ctrl+V`, then restores your snapshot. Because both the transcript and the restore are stamped, clipboard managers like Ditto and the `Win+V` history skip them entirely. The snapshot covers standard clipboard text (`CF_UNICODETEXT`), images (`CF_DIB`), and copied file lists (`CF_HDROP`). **Limitation:** app-private clipboard formats (for example a spreadsheet's internal cell format) are not snapshotted — the text/image representation survives a paste, but richer app-specific data does not.

## GPU support (AMD / non-NVIDIA)

**Source installs only.** The `--gpu` flag uses whisper.cpp via [`pywhispercpp`](https://github.com/absadiki/pywhispercpp). The pre-built wheel on PyPI is **CPU-only** — there are no precompiled Vulkan wheels, so a Vulkan-enabled build cannot be shipped as a binary and the portable ZIP does not contain one; passing `--gpu` to `WhisperPaste.exe` shows a dialog saying so and exits with code 2. To get real GPU acceleration (e.g. on an AMD GPU), install from source and rebuild `pywhispercpp` with Vulkan:

**Prerequisites**

1. Install the [Vulkan SDK](https://vulkan.lunarg.com/).
2. Install the [Visual Studio Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) (C++ compiler).

**Rebuild with Vulkan** (from the project's activated virtual environment):

```powershell
# PowerShell:
pip uninstall pywhispercpp
$env:GGML_VULKAN = "1"
pip install pywhispercpp --no-binary pywhispercpp
```

```bash
# Git Bash:
pip uninstall pywhispercpp
export GGML_VULKAN=1
pip install pywhispercpp --no-binary pywhispercpp
```

Run with `--gpu` and check the console output for Vulkan device messages. If you only see CPU references, the build didn't pick up Vulkan.

whisper.cpp models are stored under `C:\Users\<username>\AppData\Local\pywhispercpp\pywhispercpp\models\` (e.g. `ggml-small.bin`). faster-whisper models are cached under `~/.cache/huggingface/hub/`.

## Text refinement (optional)

`--refine` sends the raw transcript to a local Ollama model to fix grammar and coherence. It requires a running Ollama server with the configured model pulled:

```powershell
# One-time: install Ollama from https://ollama.com, then pull the model.
ollama pull gemma3:4b
# Ensure the server is running (ollama serve), then:
scripts\run.ps1 --refine
```

The model (`gemma3:4b`) and endpoint (`http://localhost:11434`) are set in `whisper_paste/config.py` (`OLLAMA_MODEL`, `OLLAMA_URL`). If Ollama is unavailable, WhisperPaste logs a warning and pastes the raw transcript instead of failing.

**Startup check.** With `--refine`, WhisperPaste queries the endpoint at startup and logs what it found — server version, and whether `OLLAMA_MODEL` is actually pulled. A stopped server or a missing model shows up immediately instead of silently degrading to raw transcripts at your first dictation. This detects misconfiguration; it does not authenticate the peer, since anything listening on the port can claim to be Ollama.

**Output validation.** Ollama's API is unauthenticated and its reply is pasted at your cursor, so the response is checked before it is used. Blank lines, `- ` bullets, `1. ` numbered lists and tabs are all allowed — that formatting is the reason to run the refiner. Rejected are ANSI escapes and other control characters (which can rewrite a terminal's display or overwrite text once pasted), invisible bidi/zero-width characters, and absurdly long replies. Anything rejected falls back to the raw transcript.

The prompt (`REFINER_PROMPT` in `config.py`) asks for paragraphs, bulleted and numbered lists, quotation marks, and normalised numbers/dates/units, and instructs the model to apply spoken commands like "new paragraph" or "question mark". It also tells the model that the dictated text is content to clean up, never an instruction to follow — so dictating "what is the capital of France" gets you that sentence, tidied, rather than an answer.

## Troubleshooting

- **Portable ZIP: start with `debug-console.cmd`.** `WhisperPaste.exe` is a windowed program — it has no console even when you launch it from one, so it can never print an error at you. `debug-console.cmd` runs the identical `WhisperPaste-debug.exe`, forwards any flags you give it, and keeps the window open with the exit code (`1` = already running, `2` = bad command line or `--gpu`). This is the first thing to try when a double-click appears to do nothing.
- **Logs:** the app logs to a rotating file at `%LOCALAPPDATA%\WhisperPaste\logs\whisper-paste.log` (the resolved path is logged at startup; override with `--log-dir`), and to the console when it has one. This is the first place to look whenever there is no terminal — the portable `WhisperPaste.exe` and `scripts\run.ps1 -NoConsole` both run without one. Logs hold no dictated text unless you pass `--log-transcripts`, so they are safe to share.
- **The tray icon never turns green** (the tooltip stays on "Loading model…", or shows a model-load error). The model failed to load or is still loading. Hover the icon for the short reason, then read `%LOCALAPPDATA%\WhisperPaste\logs\whisper-paste.log` for the full traceback — a missing `models\` folder, a model name that isn't bundled and can't be downloaded, or a disk/permission problem in the unpack location are the usual causes.
- **The hotkey does nothing in one particular window.** If that window belongs to a program running as administrator (Task Manager, an elevated terminal, some installers), Windows' UIPI blocks it: a normally-privileged process cannot hook input destined for an elevated window, nor inject `Ctrl+V` into one. WhisperPaste keeps working everywhere else. To dictate into elevated windows, run WhisperPaste as administrator too — with the usual caveat that it then runs with those rights.
- **SmartScreen blocked it** ("Windows protected your PC"). The build is unsigned; choose **More info → Run anyway**, and see [Windows SmartScreen and antivirus](#windows-smartscreen-and-antivirus) for what you can verify first.
- **"WhisperPaste is already running — exiting."** The single-instance guard found another running copy. Quit the existing tray instance first (or check that a previous `-NoConsole` or portable launch is still running); in the portable build this is shown as a dialog too.
- **Microphone errors** are shown in the tray icon tooltip (hover over the icon), and the app stays idle so you can fix the mic and try again.

## Development

Package layout:

```
whisper-paste/
├── whisper_paste/          # application package
│   ├── __main__.py         # entry point for `python -m whisper_paste`
│   ├── app.py              # tray, global hotkey, state machine
│   ├── recorder.py         # microphone capture → numpy array (no temp WAV)
│   ├── transcriber.py      # Whisper (faster-whisper / whisper.cpp) + preload
│   ├── refiner.py          # optional Ollama text cleanup
│   ├── clipboard_paste.py  # output: clipboard paste (+ restore) or typing
│   ├── clipboard_win.py    # Win32 clipboard snapshot/restore, ignore-stamped
│   ├── power_monitor.py    # re-register hotkey/tray after sleep/resume
│   ├── bundle.py           # frozen-build awareness (bundled model dir, --gpu refusal)
│   ├── settings.py         # whisper-paste.ini reader (defaults < ini < CLI flags)
│   └── config.py           # defaults (mutated by the ini and CLI flags at startup)
├── packaging/              # portable build: spec, model manifest, icon, notices
├── scripts/                # install.ps1 / run.ps1 / build.ps1 (+ .bat wrappers)
├── tests/                  # pytest suite
├── pyproject.toml          # package metadata, console script
├── requirements.txt        # runtime dependencies
├── requirements-build.txt  # fully pinned inputs for the portable build
└── requirements-dev.txt    # test dependencies
```

Install the dev dependencies and run the test suite from the virtual environment:

```powershell
scripts\install.ps1 -Dev
.venv\Scripts\python.exe -m pytest
```

After an editable install (`.venv\Scripts\python.exe -m pip install -e .`), the console script `whisper-paste` becomes available as an equivalent entry point to `python -m whisper_paste`.

### Building the portable ZIP

From a clean clone, one command does everything — build venv, model download, PyInstaller, licence harvest, ZIP:

```powershell
scripts\build.ps1                            # → dist\WhisperPaste-1.0.0-win64-small.zip
scripts\build.ps1 -Model small -Clean        # same, from scratch
scripts\build.ps1 -Languages en,es,fr,pt-br  # which WhisperPaste-<lang>.cmd launchers to ship
```

- **`-Model` must name a key in `packaging\models.json`** (`small` is the only one pinned today, and the default). Adding another means running `scripts\build.ps1 -Model <name> -WriteHashes`, checking the printed repository, revision and SHA-256 values against huggingface.co, and committing them to that manifest.
- **Python 3.11 is required.** `requirements-build.txt` carries a `BUILD_PYTHON = 3.11` line and the script refuses any other minor version unless you pass `-AllowPythonMismatch`. The reason is that the file pins package *versions*, not wheel tags: a different Python minor resolves different wheels for identical version strings, so the artifact would quietly stop being the one that was tested.
- **Your `.venv` is never touched.** The build works in `build\venv`, deliberately not the development venv — that is what keeps a locally rebuilt `pywhispercpp`, or anything else a contributor happens to have installed, out of a shipped ZIP.
- **The model is verified, not just downloaded.** It lands in the `build\models` cache and is checked file by file against the SHA-256 values in `packaging\models.json`, in PowerShell, independently of the library that fetched it. `-Clean` never deletes that cache.
- **`-Languages` decides which one-click launchers ship** (default `en,es`). Each code becomes a `WhisperPaste-<code>.cmd` expanded from `packaging\launcher-template.cmd`; codes are validated up front, and `-Languages ''` ships none. `packaging\whisper-paste.ini` is staged next to the exe on every build.
- All parameters: `-Model`, `-Clean` (rebuild the venv, work dir and dist tree), `-SkipZip` (leave `dist\WhisperPaste\` uncompressed while iterating), `-Languages`, `-AllowPythonMismatch`, `-WriteHashes`, `-OutputDir`.
- PyInstaller itself takes about 90 seconds; a full run including the model download and compression takes a few minutes. The ZIP lands in `dist\` (or `-OutputDir`) and the script prints its SHA-256 at the end — publish that with the release, because the README tells users to check it.

## License

WhisperPaste is released under the [MIT License](LICENSE). It depends on [pystray](https://github.com/moses-palmer/pystray) (LGPLv3) as an unmodified library dependency; all other dependencies are permissively licensed (MIT / BSD / PSF / HPND).

The portable ZIP additionally *redistributes* compiled copies of those dependencies, so it ships a `licenses\` folder generated at build time from the exact package versions in that build (36 of them), plus `THIRD-PARTY-NOTICES.md`. Two of them are copyleft: **pystray** (LGPLv3), whose complete corresponding source as bundled is included under `licenses\pystray\src\`, and the **FFmpeg** shared libraries carried by PyAV (LGPL 2.1+), shipped unmodified as separate DLLs in `_internal\av.libs\`. Both are relinkable by rebuilding the bundle with the public `scripts\build.ps1`, which uses no private tooling, key or build server. Whisper model weights are covered by their own HuggingFace model page, not by any of the above.
