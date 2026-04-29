# Voice Dictation Tool

A global dictation tool for Windows. Press a hotkey anywhere, speak, and the transcribed text is pasted at your cursor position. Runs fully locally — no cloud services needed.

## Features

- **Global hotkey** (`Ctrl+Shift+Space`) works in any application
- **Two transcription engines**: faster-whisper (CPU) or whisper.cpp (GPU/Vulkan for AMD GPUs)
- **Optional LLM refinement**: uses Ollama + Gemma to fix grammar and make dictated text coherent
- **System tray icon** with color feedback: green (ready), red (recording), blue (processing)
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

# Set language (skips auto-detection, faster):
python app.py --lang en

# Use whisper.cpp backend (GPU/Vulkan for AMD GPUs):
python app.py --gpu

# Enable LLM text cleanup (requires Ollama running):
python app.py --refine

# Use clipboard paste instead of direct typing:
python app.py --clipboard

# Combine flags:
python app.py --gpu --lang en --refine
```

### How it works

1. Press `Ctrl+Shift+Space` — tray icon turns **red** (recording)
2. Speak your message
3. Press `Ctrl+Shift+Space` again — icon turns **blue** (processing)
4. Cleaned text is **typed at your cursor** (no clipboard used by default) — icon goes back to **green**

### Stopping the app

- **Right-click** the tray icon (green/red/blue circle near the clock) and click **Quit**
- Or press `Ctrl+C` in the terminal

## CLI Flags

| Flag | Description |
|------|-------------|
| `--gpu` | Use whisper.cpp with Vulkan GPU acceleration instead of faster-whisper on CPU |
| `--refine` | Enable Ollama/Gemma text refinement (fixes grammar, coherence) |
| `--lang CODE` | Set language (e.g. `en`, `es`, `fr`). Skips auto-detection for faster results |
| `--clipboard` | Use clipboard (Ctrl+V) instead of direct typing. Faster but leaves text in clipboard history |

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
├── app.py              # Main entry point, hotkey listener, system tray
├── recorder.py         # Audio recording from microphone
├── transcriber.py      # Whisper transcription (faster-whisper or whisper.cpp)
├── refiner.py          # Ollama/Gemma text cleanup
├── clipboard_paste.py  # Clipboard + paste simulation
├── power_monitor.py    # Re-registers hotkey/tray after Windows sleep/resume
├── config.py           # Settings
└── requirements.txt    # Python dependencies
```
