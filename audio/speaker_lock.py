"""Whose voice is that? A small speaker-recognition network (WeSpeaker ResNet34, ONNX, 26 MB), used so
that the robot listens to ONE person: the visitor who started the turn.

Voice-activity detection (``voice_activity.py``) says "this is a human voice"; it cannot say whose. A
second person chatting beside the visitor, or a presenter on a loudspeaker, is a voice too. The network
turns about a second of speech into 256 numbers that describe the speaker; two clips from the same person
land close together (cosine similarity high), clips from different people land apart.

It is only ever a gate, like the voice detector: it can say "this is not the visitor's voice", and "no
opinion" (model missing, too little speech, any error) always means "treat it as the visitor". Rejecting
the visitor is worse than letting a neighbour through.

Features: Kaldi-style 80-bin log-mel filterbank (25 ms Hamming window, 10 ms step, no dither, pre-emphasis
0.97, waveform on the int16 scale), mean-normalised over the clip, exactly what the model was trained on.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
FRAME = 400  # 25 ms
HOP = 160  # 10 ms
N_FFT = 512
N_MELS = 80
MIN_SPEECH_S = 0.6  # less than this does not carry enough of a voice to compare
MODEL_REPO = "onnx-community/wespeaker-voxceleb-resnet34-LM"
MODEL_FILE = "onnx/model.onnx"

_EPS = float(np.finfo(np.float32).eps)


def _mel(hz: np.ndarray) -> np.ndarray:
    return 1127.0 * np.log(1.0 + hz / 700.0)


def _mel_bank() -> np.ndarray:
    """Kaldi's triangular filters in the mel scale, 20 Hz to Nyquist; shape (80, N_FFT // 2)."""
    low, high = 20.0, SAMPLE_RATE / 2.0
    mel_points = np.linspace(_mel(np.array(low)), _mel(np.array(high)), N_MELS + 2)
    bins_hz = np.arange(N_FFT // 2) * SAMPLE_RATE / N_FFT
    bins_mel = _mel(bins_hz)
    left, centre, right = mel_points[:-2, None], mel_points[1:-1, None], mel_points[2:, None]
    up = (bins_mel[None, :] - left) / (centre - left)
    down = (right - bins_mel[None, :]) / (right - centre)
    return np.maximum(0.0, np.minimum(up, down)).astype(np.float32)


_BANK = _mel_bank()
_WINDOW = np.hamming(FRAME).astype(np.float32)


def fbank(audio: np.ndarray) -> np.ndarray | None:
    """(frames, 80) features of 16 kHz mono float audio in [-1, 1], or None if it is too short."""
    wave = np.asarray(audio, dtype=np.float32) * 32768.0
    count = 1 + (len(wave) - FRAME) // HOP
    if count < 9:
        return None
    index = np.arange(FRAME)[None, :] + HOP * np.arange(count)[:, None]
    frames = wave[index]
    frames = frames - frames.mean(axis=1, keepdims=True)
    frames = np.concatenate([frames[:, :1], frames[:, 1:] - 0.97 * frames[:, :-1]], axis=1)
    frames = frames * _WINDOW
    spectrum = np.abs(np.fft.rfft(frames, n=N_FFT, axis=1))[:, : N_FFT // 2] ** 2
    features = np.log(np.maximum(spectrum @ _BANK.T, _EPS))
    return (features - features.mean(axis=0, keepdims=True)).astype(np.float32)


def find_model_file(models_dir: str | Path) -> Path | None:
    """The downloaded ONNX file under the program's models folder, if it is there."""
    root = Path(models_dir) / "models--onnx-community--wespeaker-voxceleb-resnet34-LM" / "snapshots"
    if not root.is_dir():
        return None
    for snapshot in sorted(root.iterdir()):
        path = snapshot / MODEL_FILE
        if path.is_file():
            return path
    return None


def download_model(models_dir: str | Path) -> Path | None:
    """Fetch the model once (26 MB). Never raises: without it the speaker gate is simply off."""
    try:
        from huggingface_hub import hf_hub_download

        return Path(hf_hub_download(MODEL_REPO, MODEL_FILE, cache_dir=str(models_dir)))
    except Exception:  # noqa: BLE001
        logger.warning("Speaker model could not be downloaded", exc_info=True)
        return None


class SpeakerEmbedder:
    """``embed(audio)`` -> a unit-length 256-number voice print, or None (no opinion). Never raises."""

    def __init__(self, model_factory: Callable[[], object] | None = None, models_dir: str | Path | None = None) -> None:
        self._factory = model_factory
        self._models_dir = models_dir
        self._session = None
        self._failed = False
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return self._session is not None and not self._failed

    def load(self) -> bool:
        with self._lock:
            if self._session is not None or self._failed:
                return self._session is not None
            try:
                if self._factory is not None:
                    self._session = self._factory()
                else:
                    path = find_model_file(self._models_dir) if self._models_dir else None
                    if path is None:
                        raise FileNotFoundError("speaker model not downloaded")
                    import onnxruntime as ort

                    options = ort.SessionOptions()
                    options.intra_op_num_threads = 2  # leave the processor to speech-to-text
                    options.inter_op_num_threads = 1
                    self._session = ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
            except Exception:  # noqa: BLE001 - optional feature
                logger.warning("Speaker recognition not available; every voice is treated as the visitor's", exc_info=True)
                self._failed = True
                return False
        return True

    def embed(self, audio: np.ndarray) -> np.ndarray | None:
        if not self.available:
            return None
        features = fbank(audio)
        if features is None:
            return None
        try:
            if hasattr(self._session, "run"):
                out = self._session.run(None, {"input_features": features[None]})[0]
            else:
                out = self._session(features[None])
            vector = np.asarray(out, dtype=np.float32).reshape(-1)
            norm = float(np.linalg.norm(vector))
            return vector / norm if norm > 0 else None
        except Exception:  # noqa: BLE001 - never stop the microphone loop
            logger.warning("Speaker recognition failed; switching it off", exc_info=True)
            self._failed = True
            return None


def similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b))


# Measured on 8 Nov 2026 with the robot's own voices (Thai; Windows Zira and David; the Thai voice pitched
# up 25% and down 20%), whole sentences and one-second windows:
#
#     same voice, different sentences             0.62 - 0.87   (one-second windows 0.62 - 0.72)
#     same voice, 10 dB or 5 dB of added hiss     0.51 - 0.53
#     same voice at a tenth of the volume         0.75
#     different voices                            -0.02 - 0.40  (0.40 only for the Thai voice against ITSELF
#                                                                pitch-shifted; other pairs stay under 0.2)
#
# A window below 0.35 is "someone else"; anything above counts as the visitor. Real people sit between
# these numbers, and the cost of a wrong "someone else" is higher than that of a wrong "visitor".
OTHER_BELOW = 0.35
ENROLL_AGAIN_ABOVE = 0.55  # a clearly matching window refines the voice print a little
EXPIRES_AFTER_S = 120.0  # a voice print this old, with nobody matching it since, is forgotten

VISITOR = "visitor"
OTHER = "other"


class SpeakerLock:
    """Remembers the voice of the visitor being listened to, and says whether a window of audio is theirs.

    The first clear second of speech after a (re)set is taken to be the visitor. ``classify`` is cheap and
    runs on the main thread; the network (``embedder.embed``) runs in the thread pool.
    """

    def __init__(self, embedder: SpeakerEmbedder, other_below: float = OTHER_BELOW) -> None:
        self.embedder = embedder
        self._other_below = other_below
        self._print: np.ndarray | None = None
        self._seen_at = 0.0

    @property
    def available(self) -> bool:
        return self.embedder.available

    @property
    def enrolled(self) -> bool:
        return self._print is not None

    def idle_seconds(self, now: float) -> float:
        """How long since a voice matched the one remembered (infinite when none is remembered)."""
        return now - self._seen_at if self._print is not None else float("inf")

    def reset(self) -> None:
        self._print = None

    def classify(self, embedding: np.ndarray | None, now: float) -> str | None:
        """VISITOR, OTHER, or None (no opinion). The first usable window enrols the visitor."""
        if embedding is None:
            return None
        if self._print is not None and now - self._seen_at > EXPIRES_AFTER_S:
            self._print = None  # a new visitor, most likely
        if self._print is None:
            self._print, self._seen_at = embedding, now
            return VISITOR
        score = similarity(self._print, embedding)
        if score < self._other_below:
            return OTHER
        self._seen_at = now
        if score >= ENROLL_AGAIN_ABOVE:
            mixed = 0.85 * self._print + 0.15 * embedding
            self._print = mixed / float(np.linalg.norm(mixed))
        return VISITOR
