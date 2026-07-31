"""Speech-to-text transcription — supports faster-whisper (CPU) and whisper.cpp (GPU/Vulkan)."""

import logging
import threading

import numpy as np

from whisper_paste import bundle
from whisper_paste import config

logger = logging.getLogger(__name__)

_model = None
_backend = None
_model_lock = threading.Lock()


def _resolve_faster_whisper_model():
    """What to hand WhisperModel: a bundled directory, else the configured name.

    `config.WHISPER_MODEL` is read here, at call time, not at import time —
    `main()` rewrites config attributes from the CLI after this module is
    imported (see CLAUDE.md, "Config module is mutated at startup").

    Returning a directory is legitimate: faster_whisper's WhisperModel checks
    `os.path.isdir(model_size_or_path)` before falling back to
    `download_model()`, so a bundled model dir short-circuits the HuggingFace
    download. When nothing shipped for this name, the bare name goes through
    unchanged and downloads as usual.
    """
    name = config.WHISPER_MODEL
    return bundle.bundled_model_dir(name) or name


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
            # Reject --gpu here, *before* importing pywhispercpp: the portable
            # build ships no Vulkan-enabled pywhispercpp, so the import would
            # fail with a bare ModuleNotFoundError. This is the one chokepoint
            # every transcription path crosses, so raising a RuntimeError with
            # an actionable message means it reaches the user as a tray tooltip
            # via _preload_model in app.py. Keep the check above the import.
            if bundle.is_frozen():
                raise RuntimeError(bundle.GPU_UNSUPPORTED_MESSAGE)

            from pywhispercpp.model import Model

            logger.info("Loading whisper.cpp model '%s' (GPU/Vulkan)...", config.WHISPER_MODEL)
            _model = Model(config.WHISPER_MODEL)
            logger.info("whisper.cpp model loaded.")
        else:
            _backend = "faster-whisper"
            from faster_whisper import WhisperModel

            # A bundled model directory when one shipped, else the bare name.
            model_ref = _resolve_faster_whisper_model()
            logger.info("Loading faster-whisper model '%s' on CPU...", model_ref)
            _model = WhisperModel(model_ref, device="cpu", compute_type="int8")
            logger.info("faster-whisper model loaded.")

    return _model


def transcribe(audio) -> str:
    """Transcribe a 1-D float32 numpy array (16kHz mono) and return the raw text."""
    model = _get_model()

    if _backend == "whisper.cpp":
        # pywhispercpp's Model.transcribe accepts a float32 numpy array directly.
        # "" means auto-detect, matching the faster-whisper branch's language=None.
        # It has to be passed explicitly: whisper.cpp defaults params.language to
        # "en", so omitting the kwarg silently forces English instead of detecting.
        # "auto" is not usable here — whisper_lang_id() doesn't know it, and the
        # binding's setter drops it, leaving "" behind anyway.
        segments = model.transcribe(audio, language=config.WHISPER_LANGUAGE or "")
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
