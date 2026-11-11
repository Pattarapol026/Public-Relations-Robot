"""Is that a human voice? A small neural detector (Silero VAD), used so that noise never counts as speech.

The level of the microphone cannot tell a person from a fan, a door or a chair. Left to the level
alone, steady noise keeps a turn open until the recording limit (and a click can start a turn that
only ever transcribes nothing). Silero VAD ships with faster-whisper as a 1.8 MB ONNX file and judges
half a second of audio in under a millisecond on the processor.

Measured on 7 Nov 2026 (Thai speech from the robot's own voice, at four volumes, and made-up noises;
the highest probability within each half-second window):

    speech at full, 0.3, 0.1 and 0.03 of its volume    0.95-1.00   (0.63 in the first window of the quietest)
    speech 0.3 + steady hiss                            0.54-0.98
    hiss 0.05 / 0.2, a 120 Hz hum, a fan, clicks        0.00-0.19   (0.48 once, for loud hiss)

So 0.5 separates them with room to spare. It is only a gate on the level: it can say "this loud
sound is not speech", and never makes the robot hear anything it did not.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

import numpy as np

logger = logging.getLogger(__name__)

CHUNK = 512  # samples per judgement (32 ms at 16 kHz), the size the model takes
SAMPLE_RATE = 16_000
WINDOW_S = 0.5
THRESHOLD = 0.5


class VoiceActivityDetector:
    """``is_voice(audio)`` for the last half second. Unavailable means "no opinion", never "no voice"."""

    def __init__(self, model_factory: Callable[[], object] | None = None, threshold: float = THRESHOLD) -> None:
        self._factory = model_factory
        self._threshold = threshold
        self._model = None
        self._failed = False
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return self._model is not None and not self._failed

    def load(self) -> bool:
        """Load the model (call on the main thread at start-up: it imports faster-whisper). Never raises."""
        with self._lock:
            if self._model is not None or self._failed:
                return self._model is not None
            try:
                if self._factory is not None:
                    self._model = self._factory()
                else:
                    from faster_whisper.vad import get_vad_model

                    self._model = get_vad_model()
            except Exception:  # noqa: BLE001 - optional; without it the level alone decides, as before
                logger.warning("Voice detector not available; noise is told from speech by level only", exc_info=True)
                self._failed = True
                return False
        return True

    def probability(self, audio: np.ndarray) -> float | None:
        """The highest speech probability within the last half second of 16 kHz mono audio."""
        if not self.available:
            return None
        wanted = int(WINDOW_S * SAMPLE_RATE)
        window = np.asarray(audio[-wanted:], dtype=np.float32)
        usable = len(window) // CHUNK * CHUNK
        if usable < CHUNK * 4:
            return None  # too little to judge
        try:
            out = self._model(np.ascontiguousarray(window[len(window) - usable:]))
            return float(np.max(np.asarray(out)))
        except Exception:  # noqa: BLE001 - never stop the microphone loop
            logger.warning("Voice detector failed; switching it off", exc_info=True)
            self._failed = True
            return None

    def is_voice(self, audio: np.ndarray) -> bool | None:
        """True: speech. False: sound that is not speech. None: cannot tell (then treat it as speech)."""
        probability = self.probability(audio)
        return None if probability is None else probability >= self._threshold
