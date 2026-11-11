"""Speaks text: synthesises it piece by piece in the thread pool and plays it as it arrives."""
from __future__ import annotations

import logging
from enum import Enum
from typing import Callable

import numpy as np
from PySide6.QtCore import QObject, QThreadPool, QTimer, Signal, Slot

from app import turn_timing
from app.workers import FunctionWorker
from audio.microphone import AudioDevice
from audio.text_to_speech import TextToSpeechService, speech_pieces
from services.audio_playback_service import AudioPlaybackService, PlaybackError

logger = logging.getLogger(__name__)

POLL_MS = 50
# Pauses like a person's: a breath after a sentence, a short one where a long sentence
# had to be cut (the Thai model takes about 140 characters at a time). Each piece's own
# silence is trimmed first: the Thai model ends some pieces with 0.17 s of it and others
# with none, which made the same kind of pause long one time and short the next.
PIECE_PAUSE_S = 0.35  # after a sentence
MID_SENTENCE_PAUSE_S = 0.12  # a long sentence cut in two
# A voice change is usually a Thai name inside an English sentence.
VOICE_CHANGE_PAUSE_S = 0.06
SILENCE_LEVEL = 0.004  # low: the fading end of "ครับ" must stay
EDGE_S = 0.05  # kept on each side of the speech, so no sound is clipped
_SENTENCE_ENDINGS = ("ครับ", "ค่ะ", "คะ", "นะ", "จ้ะ", "จ้า", ".", "!", "?", "…", "。")
NO_AUDIO_MESSAGE = "สร้างเสียงพูดไม่สำเร็จ (ไม่มีเสียงออกมา)"


class SpeechStatus(Enum):
    DISABLED = "disabled"
    LOADING = "loading"
    READY = "ready"
    UNAVAILABLE = "unavailable"


_STATUS_TEXT = {
    SpeechStatus.DISABLED: "ปิดใช้งาน",
    SpeechStatus.LOADING: "กำลังโหลดโมเดล...",
    SpeechStatus.READY: "พร้อม",
    SpeechStatus.UNAVAILABLE: "ใช้งานไม่ได้",
}


class SpeechController(QObject):
    status_changed = Signal()
    finished = Signal()  # everything passed to speak() has left the speaker
    failed = Signal(str)  # speaking stopped because of an error (message for the log/Admin)

    def __init__(
        self,
        enabled: bool,
        tts: TextToSpeechService,
        playback: AudioPlaybackService,
        output_device: Callable[[], str | None],
        pool: QThreadPool,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._tts = tts
        self._playback = playback
        self._output_device = output_device
        self._pool = pool
        self._status = SpeechStatus.DISABLED
        self._detail = ""
        # Bumped by every speak() and stop(); pieces carrying an older token are dropped.
        self._token = 0
        self._pieces: list[tuple[str, str]] = []  # (text, language)
        self._next_chunk = 0
        self._fed_samples = 0
        self._speed: float | None = None
        self._speaking = False
        # Shown as unavailable after an error while speaking, but the next answer still
        # tries, and a success puts it right: unlike a model that never loaded, it may pass.
        self._speaking_failed = False

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(POLL_MS)
        self._poll_timer.timeout.connect(self._poll)
        if enabled:
            self._preload_model()

    @property
    def status(self) -> SpeechStatus:
        return self._status

    @property
    def status_text(self) -> str:
        text = _STATUS_TEXT[self._status]
        return f"{text} — {self._detail}" if self._detail else text

    @property
    def is_ready(self) -> bool:
        return self._status is SpeechStatus.READY

    @property
    def is_speaking(self) -> bool:
        return self._speaking

    def output_devices(self) -> list[AudioDevice]:
        """Raises ``PlaybackError`` if the audio system cannot be queried."""
        return self._playback.list_output_devices()

    def set_speed(self, speed: float) -> None:
        self._tts.set_speed(speed)

    def speak(self, text: str, speed: float | None = None) -> bool:
        """Start speaking ``text``. Returns False if speech cannot be used right now.

        ``finished`` or ``failed`` follows, unless ``stop`` is called first.
        ``speed`` overrides the configured speed for this text only.
        """
        self._speed = speed
        self.stop()
        if not self.is_ready and not self._speaking_failed:
            return False
        pieces = speech_pieces(text)
        if not pieces:
            return False
        device = self._output_device()
        logger.info("Speaking %d characters through %s", len(text), device or "the system's default speaker")
        try:
            self._playback.start(self._tts.sample_rate, device)
        except PlaybackError as exc:
            logger.error("Cannot speak: %s", exc)
            self._mark_failed(str(exc))
            return False
        self._pieces = pieces
        self._next_chunk = 0
        self._fed_samples = 0
        self._speaking = True
        self._synthesize_next()
        self._poll_timer.start()
        return True

    def stop(self) -> None:
        """Stop speaking at once. No ``finished`` signal follows."""
        self._token += 1
        self._speaking = False
        self._poll_timer.stop()
        self._playback.stop()

    # --- internals ---

    def _preload_model(self) -> None:
        self._set_status(SpeechStatus.LOADING)
        worker = FunctionWorker(None, self._tts.load)
        worker.signals.finished.connect(self._on_model_loaded)
        worker.signals.failed.connect(self._on_model_failed)
        self._pool.start(worker)

    @Slot(object, object)
    def _on_model_loaded(self, _token: object, _result: object) -> None:
        self._set_status(SpeechStatus.READY)

    @Slot(object, str)
    def _on_model_failed(self, _token: object, error: str) -> None:
        self._set_status(SpeechStatus.UNAVAILABLE, error)

    def _set_status(self, status: SpeechStatus, detail: str = "") -> None:
        self._status, self._detail = status, detail
        logger.info("Speech output status: %s %s", status.value, detail)
        self.status_changed.emit()

    def _mark_failed(self, error: str) -> None:
        self._speaking_failed = True
        self._set_status(SpeechStatus.UNAVAILABLE, error)

    def _fail(self, error: str) -> None:
        self.stop()
        self._mark_failed(error)
        self.failed.emit(error)

    def _synthesize_next(self) -> None:
        text, language = self._pieces[self._next_chunk]
        worker = FunctionWorker(self._token, self._tts.synthesize, text, self._speed, language)
        worker.signals.finished.connect(self._on_chunk_ready)
        worker.signals.failed.connect(self._on_chunk_failed)
        self._pool.start(worker)

    def _with_pause(self, audio: np.ndarray) -> np.ndarray:
        """``audio`` of the current piece, trimmed, after an even pause if another piece came before it."""
        index = self._next_chunk
        audio = _trimmed(audio, self._tts.sample_rate)
        if index == 0 or len(audio) == 0:
            return audio
        previous_text, previous_voice = self._pieces[index - 1]
        if self._pieces[index][1] != previous_voice:
            seconds = VOICE_CHANGE_PAUSE_S
        elif previous_text.rstrip().endswith(_SENTENCE_ENDINGS):
            seconds = PIECE_PAUSE_S
        else:
            seconds = MID_SENTENCE_PAUSE_S
        return np.concatenate((np.zeros(int(seconds * self._tts.sample_rate), dtype=np.float32), audio))

    @Slot(object, object)
    def _on_chunk_ready(self, token: int, audio: object) -> None:
        if token != self._token:
            return
        self._fed_samples += len(audio)
        self._playback.feed(self._with_pause(audio), self._tts.sample_rate)
        if self._next_chunk == 0:
            turn_timing.finish()  # the first words are on their way to the speaker
        self._next_chunk += 1
        if self._next_chunk < len(self._pieces):
            self._synthesize_next()
        elif self._fed_samples == 0:
            self._fail(NO_AUDIO_MESSAGE)
        else:
            self._playback.finish()

    @Slot(object, str)
    def _on_chunk_failed(self, token: int, error: str) -> None:
        if token != self._token:
            return
        self._fail(error)

    def _poll(self) -> None:
        if not self._speaking:
            return
        failure = self._playback.failure
        if failure:
            logger.error("Playback failed: %s", failure)
            self._fail(failure)
        elif not self._playback.is_active:
            self._speaking = False
            self._poll_timer.stop()
            self._playback.stop()
            if self._speaking_failed:
                self._speaking_failed = False
                self._set_status(SpeechStatus.READY)
            self.finished.emit()


def _trimmed(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    """``audio`` without its leading and trailing silence, keeping ``EDGE_S`` of it on each side."""
    loud = np.flatnonzero(np.abs(audio) > SILENCE_LEVEL)
    if len(loud) == 0:
        return audio
    edge = int(EDGE_S * sample_rate)
    return audio[max(0, loud[0] - edge):loud[-1] + 1 + edge]
