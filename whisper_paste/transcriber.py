"""Speech-to-text transcription — supports faster-whisper (CPU) and whisper.cpp (GPU/Vulkan)."""

import logging
import threading

import numpy as np

from whisper_paste import config

logger = logging.getLogger(__name__)

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

            logger.info("Loading whisper.cpp model '%s' (GPU/Vulkan)...", config.WHISPER_MODEL)
            _model = Model(config.WHISPER_MODEL)
            logger.info("whisper.cpp model loaded.")
        else:
            _backend = "faster-whisper"
            from faster_whisper import WhisperModel

            logger.info("Loading faster-whisper model '%s' on CPU...", config.WHISPER_MODEL)
            _model = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8")
            logger.info("faster-whisper model loaded.")

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
    except Exception:
        logger.warning("Warm-up transcription failed (continuing anyway)", exc_info=True)
