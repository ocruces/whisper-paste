"""Audio recording module using sounddevice."""

import numpy as np
import sounddevice as sd
from whisper_paste.config import SAMPLE_RATE, CHANNELS


class Recorder:
    def __init__(self):
        self._frames = []
        self._stream = None
        self._recording = False

    @property
    def is_recording(self):
        return self._recording

    def start(self):
        """Start recording from the default microphone.

        Only marks the recorder as recording once the InputStream has been
        created and started, so a mic failure leaves it in a clean idle state
        and the exception propagates to the caller.
        """
        self._frames = []
        stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            callback=self._callback,
        )
        stream.start()
        self._stream = stream
        self._recording = True

    def stop(self):
        """Stop recording and return a 1-D float32 numpy array (16kHz mono).

        Returns None if no audio frames were captured.
        """
        self._recording = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        if not self._frames:
            return None

        # Frames arrive shaped (n, 1) from the mono stream; flatten to 1-D.
        audio = np.concatenate(self._frames, axis=0).astype(np.float32).flatten()
        return audio

    def _callback(self, indata, frames, time_info, status):
        if self._recording:
            self._frames.append(indata.copy())
