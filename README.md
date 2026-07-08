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
- **Rotating file logs** at `logs/whisper-paste.log`.

## Requirements

- Windows 10 or 11
- Python 3.10 or newer
- A microphone

The first run downloads the Whisper model (the background preload fetches it). That needs internet **once**; after the model is cached, transcription is fully offline.

## Quick start

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

**Quit:** right-click the tray icon and choose **Quit**, or press `Ctrl+C` in the console.

### Tray icon states

| Color | State |
|-------|-------|
| 🟢 Green | Idle / ready |
| 🔴 Red | Recording |
| 🔵 Blue | Processing (transcribing / pasting) |

### Flags

Pass flags after `scripts\run.ps1` (or `python -m whisper_paste`). Defaults come from `whisper_paste/config.py`.

| Flag | Effect | Default |
|------|--------|---------|
| `--model NAME` | Whisper model to load (`tiny`, `base`, `small`, `medium`, `large-v3`, `distil-small.en`, …). | `small` |
| `--lang CODE` | **Forces** the transcription language (e.g. `en`, `es`, `fr`) — audio is transcribed as this language regardless of what is spoken. | auto-detect |
| `--gpu` | Use the whisper.cpp / Vulkan backend instead of faster-whisper on CPU (see [GPU support](#gpu-support-amd--non-nvidia)). | off (CPU) |
| `--refine` | Clean up the transcript with a local Ollama model (see [Text refinement](#text-refinement-optional)). | off |
| `--type` | Type the text character-by-character instead of pasting via the clipboard (slower, never touches the clipboard). | off (clipboard paste) |

Flags can be combined, e.g. `scripts\run.ps1 --gpu --lang en --refine`.

## How it works

The hotkey toggles recording. Audio is captured from the default microphone with [sounddevice](https://python-sounddevice.readthedocs.io/) as a 16 kHz mono float32 numpy array (no temporary WAV file), handed to Whisper for transcription, optionally refined, and then delivered at the cursor. Everything runs in a small state machine driven from the tray icon; the transcription/paste work happens on a worker thread so the tray stays responsive.

**Clipboard invisibility.** In the default paste mode, WhisperPaste snapshots your current clipboard, writes the transcript (stamped with the `Clipboard Viewer Ignore` and `ExcludeClipboardContentFromMonitorProcessing` formats), sends `Ctrl+V`, then restores your snapshot. Because both the transcript and the restore are stamped, clipboard managers like Ditto and the `Win+V` history skip them entirely. The snapshot covers standard clipboard text (`CF_UNICODETEXT`), images (`CF_DIB`), and copied file lists (`CF_HDROP`). **Limitation:** app-private clipboard formats (for example a spreadsheet's internal cell format) are not snapshotted — the text/image representation survives a paste, but richer app-specific data does not.

## GPU support (AMD / non-NVIDIA)

The `--gpu` flag uses whisper.cpp via [`pywhispercpp`](https://github.com/absadiki/pywhispercpp). The pre-built wheel on PyPI is **CPU-only** — there are no precompiled Vulkan wheels. To get real GPU acceleration (e.g. on an AMD GPU), rebuild `pywhispercpp` with Vulkan:

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

## Troubleshooting

- **Logs:** the app logs to the console and to a rotating file at `logs/whisper-paste.log` (in the repo root). This is the first place to look, especially under `-NoConsole` where there's no terminal.
- **"WhisperPaste is already running — exiting."** The single-instance guard found another running copy. Quit the existing tray instance first (or check that a previous `-NoConsole` launch is still running).
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
│   └── config.py           # defaults (mutated by CLI flags at startup)
├── scripts/                # install.ps1 / run.ps1 (+ .bat wrappers)
├── tests/                  # pytest suite
├── pyproject.toml          # package metadata, console script
├── requirements.txt        # runtime dependencies
└── requirements-dev.txt    # test dependencies
```

Install the dev dependencies and run the test suite from the virtual environment:

```powershell
scripts\install.ps1 -Dev
.venv\Scripts\python.exe -m pytest
```

After an editable install (`.venv\Scripts\python.exe -m pip install -e .`), the console script `whisper-paste` becomes available as an equivalent entry point to `python -m whisper_paste`.

## License

WhisperPaste is released under the [MIT License](LICENSE). It depends on [pystray](https://github.com/moses-palmer/pystray) (LGPLv3) as an unmodified library dependency; all other dependencies are permissively licensed (MIT / BSD / PSF / HPND).
