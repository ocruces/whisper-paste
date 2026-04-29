"""Audio recording module using sounddevice."""

import tempfile
import wave
import numpy as np
import sounddevice as sd
from config import SAMPLE_RATE, CHANNELS


class Recorder:
    def __init__(self):
        self._frames = []
        self._stream = None
        self._recording = False

    @property
    def is_recording(self):
        return self._recording

    def start(self):
        """Start recording from the default microphone."""
        self._frames = []
        self._recording = True
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> str:
        """Stop recording and return path to a temporary WAV file."""
        self._recording = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        if not self._frames:
            return None

        audio = np.concatenate(self._frames, axis=0)
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        with wave.open(tmp.name, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes((audio * 32767).astype(np.int16).tobytes())
        return tmp.name

    def _callback(self, indata, frames, time_info, status):
        if self._recording:
            self._frames.append(indata.copy())
