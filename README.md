# Voice Dictation Tool

A global dictation tool for Windows. Press a hotkey anywhere, speak, and the transcribed text is pasted at your cursor position. Runs fully locally — no cloud services needed.

## Features

- **Global hotkey** (`Ctrl+Shift+Space`) works in any application
- **Two transcription engines**: faster-whisper (CPU) or whisper.cpp (GPU/Vulkan for AMD GPUs)
- **Optional LLM refinement**: uses Ollama + Gemma to fix grammar and make dictated text coherent
- **System tray icon** with color feedback: green (ready), red (recording), blue (processing)
- **Fast first dictation** — the Whisper model is loaded and warmed up in the background at startup
- **Clipboard-safe paste** — pastes via Ctrl+V and restores your previous clipboard, staying invisible to clipboard managers (Ditto) and Windows clipboard history (Win+V)
- **Survives sleep/resume** — the global hotkey and tray icon are automatically re-registered after Windows suspends

## Prerequisites

- Python 3.10+
- (Optional) [Ollama](https://ollama.com) — only if using `--refine` mode

## Installation

### 1. Create a Python virtual environment

```bash
# Create the virtual environment
py -m venv .venv
```

Activate the environment:

| Shell | Command |
|-------|---------|
| CMD | `.venv\Scripts\activate` |
| PowerShell | `.venv\Scripts\Activate.ps1` |
| Git Bash | `source .venv/Scripts/activate` |

Verify it's active and update pip:

```bash
pip list
python -m pip install --upgrade pip
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. (Optional) Install Ollama for text refinement

Only needed if using `--refine` mode:

1. Download and install [Ollama](https://ollama.com)
2. Pull the Gemma model:
   ```bash
   ollama pull gemma3:4b
   ```

### Virtual environment reference

| Task | Command |
|------|---------|
| Create environment | `py -m venv .venv` |
| Activate (CMD) | `.venv\Scripts\activate` |
| Activate (PowerShell) | `.venv\Scripts\Activate.ps1` |
| Activate (Git Bash) | `source .venv/Scripts/activate` |
| Install a package | `pip install package` |
| Install specific version | `pip install "package==version"` |
| Deactivate | `deactivate` |

## Usage

```bash
# Default — faster-whisper on CPU, no LLM:
python app.py

# Force a language (skips auto-detection; transcribes AS this language):
python app.py --lang en

# Pick a different Whisper model:
python app.py --model distil-small.en

# Use whisper.cpp backend (GPU/Vulkan for AMD GPUs):
python app.py --gpu

# Enable LLM text cleanup (requires Ollama running):
python app.py --refine

# Type character-by-character instead of pasting via the clipboard:
python app.py --type

# Combine flags:
python app.py --gpu --lang en --refine
```

### How it works

1. Press `Ctrl+Shift+Space` — tray icon turns **red** (recording)
2. Speak your message
3. Press `Ctrl+Shift+Space` again — icon turns **blue** (processing)
4. Cleaned text is **pasted at your cursor** and your previous clipboard is restored — icon goes back to **green**

Recording auto-stops after 2 minutes (`config.MAX_RECORD_SECONDS`) as a safety cap, in case you forget to press the hotkey a second time.

### Output modes (paste vs. type)

By default the transcript is placed on the clipboard and pasted with **Ctrl+V**, then your previous clipboard contents are **restored** afterwards. The pasted text is stamped with the `Clipboard Viewer Ignore` and `ExcludeClipboardContentFromMonitorProcessing` clipboard formats, so clipboard managers (e.g. **Ditto**) and the Windows **Win+V** history never record it.

Restore covers the standard clipboard formats: **text**, **images** (CF_DIB), and **copied files** (CF_HDROP). Limitation: app-private clipboard formats (e.g. Excel's internal cell format) are **not** snapshotted — the text/image representation survives a paste, but richer app-specific data does not.

Pass `--type` to fall back to the old behavior: the text is typed **character-by-character** with no clipboard involvement (slower, but touches nothing on the clipboard).

### Stopping the app

- **Right-click** the tray icon (green/red/blue circle near the clock) and click **Quit**
- Or press `Ctrl+C` in the terminal

## CLI Flags

| Flag | Description |
|------|-------------|
| `--gpu` | Use whisper.cpp with Vulkan GPU acceleration instead of faster-whisper on CPU |
| `--refine` | Enable Ollama/Gemma text refinement (fixes grammar, coherence) |
| `--lang CODE` | Force the transcription language (e.g. `en`, `es`, `fr`). Skips auto-detection and transcribes as this language regardless of what is spoken. Default is auto-detect |
| `--model NAME` | Whisper model to load (e.g. `tiny`, `base`, `small`, `distil-small.en`). Overrides `WHISPER_MODEL` in `config.py` |
| `--type` | Type the text character-by-character instead of pasting via the clipboard. Slower, but never touches the clipboard |

## Modes Comparison

| Flags | Transcription | Text Cleanup | Speed | Memory |
|-------|--------------|--------------|-------|--------|
| *(none)* | faster-whisper (CPU) | None | Good | Low |
| `--gpu` | whisper.cpp (Vulkan) | None | Fast (with GPU) | Low |
| `--refine` | faster-whisper (CPU) | Ollama/Gemma | Slower | High |
| `--gpu --refine` | whisper.cpp (Vulkan) | Ollama/Gemma | Medium | High |

## Model Storage Locations

### Whisper models

- **faster-whisper** (default): stored in `~/.cache/huggingface/hub/`
- **whisper.cpp** (`--gpu`): stored in `C:\Users\<username>\AppData\Local\pywhispercpp\pywhispercpp\models\` (e.g. `ggml-small.bin`)

### Ollama models (only with `--refine`)

Ollama stores models in:
- Windows: `C:\Users\<username>\.ollama\models\`

To delete models and free disk space:

```bash
# List Ollama models and sizes:
ollama list

# Remove a specific model:
ollama rm gemma3:4b
```

## GPU Support (AMD / Non-NVIDIA)

The `--gpu` flag uses whisper.cpp via `pywhispercpp`. The pre-built pip wheel from PyPI is **CPU only** — there are no precompiled Vulkan wheels available.

To get actual GPU acceleration with an AMD GPU, rebuild `pywhispercpp` with Vulkan:

### Prerequisites

1. Install the [Vulkan SDK](https://vulkan.lunarg.com/)
2. Install [Visual Studio Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) (C++ compiler)

### Build with Vulkan

```bash
# CMD:
pip uninstall pywhispercpp
set GGML_VULKAN=1
pip install pywhispercpp --no-binary pywhispercpp

# Git Bash:
pip uninstall pywhispercpp
export GGML_VULKAN=1
pip install pywhispercpp --no-binary pywhispercpp
```

### Verify GPU is being used

Run with `--gpu` and check the console output for Vulkan device messages. If you only see CPU references, the build didn't pick up Vulkan.

## Configuration

Edit `config.py` to change defaults:

- `HOTKEY` — the global hotkey combo (default: `ctrl+shift+space`)
- `WHISPER_MODEL` — model size: `tiny`, `base`, `small`, `medium`, `large-v3`
- `WHISPER_LANGUAGE` — default language or `None` for auto-detect
- `OLLAMA_MODEL` — the LLM model for refinement (default: `gemma3:4b`)

## Project Structure

```
whisper-paste/
├── app.py                  # Main entry point, hotkey listener, system tray
├── recorder.py             # Audio recording from microphone (numpy array, no temp WAV)
├── transcriber.py          # Whisper transcription (faster-whisper or whisper.cpp) + preload/warm-up
├── refiner.py              # Ollama/Gemma text cleanup
├── clipboard_paste.py      # Output: clipboard paste (+ restore) or character typing
├── clipboard_win.py        # Win32 clipboard snapshot/restore, invisible to clipboard managers
├── power_monitor.py        # Re-registers hotkey/tray after Windows sleep/resume
├── config.py               # Settings
├── tests/                  # pytest test suite
├── requirements.txt        # Python dependencies
└── requirements-dev.txt    # Extra dependencies for running the tests
```

## Logging

The app logs to the console and to a rotating log file at `logs/whisper-paste.log` (next to `app.py`, kept to a few rotated files). This is especially useful when running under `pythonw` with no console attached — check the log file to see what happened.

## Development

There is a small pytest suite under `tests/`. Install the dev dependencies and run it from the venv:

```bash
pip install -r requirements-dev.txt
python -m pytest
```
