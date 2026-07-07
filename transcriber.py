"""Speech-to-text transcription — supports faster-whisper (CPU) and whisper.cpp (GPU/Vulkan)."""

import threading

import numpy as np

import config

_model = None
_backend = None
_model_lock = threading.Lock()


def _get_model():
    global _model, _backend

    # Fast path: model already loaded, no need to acquire the lock.
    if _model is not None:
        return _model

    with _model_lock:
        # Re-check under the lock so only the first caller loads the model.
        if _model is not None:
            return _model

        if config.USE_GPU:
            _backend = "whisper.cpp"
            from pywhispercpp.model import Model

            print(f"Loading whisper.cpp model '{config.WHISPER_MODEL}' (GPU/Vulkan)...")
            _model = Model(config.WHISPER_MODEL)
            print("whisper.cpp model loaded.")
        else:
            _backend = "faster-whisper"
            from faster_whisper import WhisperModel

            print(f"Loading faster-whisper model '{config.WHISPER_MODEL}' on CPU...")
            _model = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8")
            print("faster-whisper model loaded.")

    return _model


def transcribe(audio) -> str:
    """Transcribe a 1-D float32 numpy array (16kHz mono) and return the raw text."""
    model = _get_model()

    if _backend == "whisper.cpp":
        # pywhispercpp's Model.transcribe accepts a float32 numpy array directly.
        kwargs = {}
        if config.WHISPER_LANGUAGE:
            kwargs["language"] = config.WHISPER_LANGUAGE
        segments = model.transcribe(audio, **kwargs)
        text = " ".join(seg.text.strip() for seg in segments)
    else:
        # faster-whisper accepts numpy arrays natively.
        segments, info = model.transcribe(
            audio,
            language=config.WHISPER_LANGUAGE,
            beam_size=config.BEAM_SIZE,
            vad_filter=True,
        )
        text = " ".join(seg.text.strip() for seg in segments)

    return text.strip()


def preload():
    """Load the model and run a warm-up transcription so the first dictation is fast.

    Model-load errors propagate, but a failure of the warm-up transcription is
    logged and swallowed — it must never take the app down at startup.
    """
    _get_model()
    try:
        transcribe(np.zeros(8000, dtype=np.float32))
    except Exception as e:
        print(f"Warm-up transcription failed (continuing anyway): {e}")
