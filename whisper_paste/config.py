"""Configuration for the voice dictation tool."""

# Global hotkey to toggle recording (press to start, press again to stop)
HOTKEY = "ctrl+shift+space"

# Whisper settings
WHISPER_MODEL = "small"  # Options: tiny, base, small, medium, large-v3
WHISPER_DEVICE = "cpu"   # CPU mode (no CUDA needed)
WHISPER_LANGUAGE = None   # None = auto-detect, or set e.g. "en", "es", "fr"
USE_GPU = False          # toggled via --gpu CLI flag (uses whisper.cpp with Vulkan)
BEAM_SIZE = 5            # beam search width for the faster-whisper branch

# Ollama settings (only used when --refine flag is passed)
OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "gemma3:4b"
USE_REFINER = False  # toggled via --refine CLI flag

# Output settings
# Default: paste via the clipboard (fast, and kept out of clipboard managers /
# Win+V history) then restore the user's previous clipboard. The --type CLI flag
# switches to slow character-by-character typing.
USE_CLIPBOARD = True
# Time the target app gets to read the clipboard before we restore the previous
# contents. Too short and the paste can miss; too long delays the restore.
CLIPBOARD_RESTORE_DELAY = 0.3

# Audio settings
SAMPLE_RATE = 16000  # Whisper expects 16kHz
CHANNELS = 1         # Mono audio

# Auto-stop safety cap: force-stop and process a recording after this many
# seconds so a forgotten/stuck recording can't run forever.
MAX_RECORD_SECONDS = 120

# Logging: directory (relative to the app directory) for the rotating log file.
LOG_DIR = "logs"

# Refiner prompt
REFINER_PROMPT = (
    "You are a dictation assistant. The user dictated the following text via speech-to-text. "
    "Fix any grammar issues, typos, or incoherent parts. Make it read naturally. "
    "Output ONLY the corrected text, nothing else — no explanations, no quotes, no prefixes.\n\n"
    "Dictated text: {text}"
)
