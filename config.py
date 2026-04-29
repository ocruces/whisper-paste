"""Configuration for the voice dictation tool."""

# Global hotkey to toggle recording (press to start, press again to stop)
HOTKEY = "ctrl+shift+space"

# Whisper settings
WHISPER_MODEL = "small"  # Options: tiny, base, small, medium, large-v3
WHISPER_DEVICE = "cpu"   # CPU mode (no CUDA needed)
WHISPER_LANGUAGE = None   # None = auto-detect, or set e.g. "en", "es", "fr"
USE_GPU = False          # toggled via --gpu CLI flag (uses whisper.cpp with Vulkan)

# Ollama settings (only used when --refine flag is passed)
OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "gemma3:4b"
USE_REFINER = False  # toggled via --refine CLI flag

# Output settings
USE_CLIPBOARD = False  # default: type text directly (no clipboard). Enable via --clipboard

# Audio settings
SAMPLE_RATE = 16000  # Whisper expects 16kHz
CHANNELS = 1         # Mono audio

# Refiner prompt
REFINER_PROMPT = (
    "You are a dictation assistant. The user dictated the following text via speech-to-text. "
    "Fix any grammar issues, typos, or incoherent parts. Make it read naturally. "
    "Output ONLY the corrected text, nothing else — no explanations, no quotes, no prefixes.\n\n"
    "Dictated text: {text}"
)
