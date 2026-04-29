"""Speech-to-text transcription — supports faster-whisper (CPU) and whisper.cpp (GPU/Vulkan)."""

import config

_model = None
_backend = None


def _get_model():
    global _model, _backend

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


def transcribe(audio_path: str) -> str:
    """Transcribe an audio file and return the raw text."""
    model = _get_model()

    if _backend == "whisper.cpp":
        kwargs = {}
        if config.WHISPER_LANGUAGE:
            kwargs["language"] = config.WHISPER_LANGUAGE
        segments = model.transcribe(audio_path, **kwargs)
        text = " ".join(seg.text.strip() for seg in segments)
    else:
        segments, info = model.transcribe(
            audio_path,
            language=config.WHISPER_LANGUAGE,
            beam_size=5,
            vad_filter=True,
        )
        text = " ".join(seg.text.strip() for seg in segments)

    return text.strip()
