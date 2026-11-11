"""Speaks text: synthesises it piece by piece in the thread pool and plays it as it arrives."""
from __future__ import annotations

import logging
import threading
import time
from enum import Enum
from typing import Callable

import numpy as np
from PySide6.QtCore import QObject, QThreadPool, QTimer, Signal, Slot

from app import turn_timing
from app.workers import FunctionWorker
from audio.microphone import AudioDevice
from audio.cues import CueLibrary
from audio.text_to_speech import (
    ENGINE_EDGE,
    ENGINE_LOCAL,
    LANG_CUE,
    LANG_PAUSE,
    TextToSpeechService,
    speech_items,
)
from services.audio_playback_service import AudioPlaybackService, PlaybackError

logger = logging.getLogger(__name__)

POLL_MS = 50
# Pauses like a person's: a breath after a sentence, a short one where a long sentence
# had to be cut (the Thai model takes about 140 characters at a time). Each piece's own
# silence is trimmed first: the Thai model ends some pieces with 0.17 s of it and others
# with none, which made the same kind of pause long one time and short the next.
# Pieces are cut only where the text has a space (or a sentence ends), and the pause there is a short breath: a person
# talking does not stop as a reader does.
PIECE_PAUSE_S = 0.24  # after a sentence
QUESTION_PAUSE_S = 0.30
EXCLAMATION_PAUSE_S = 0.26
ELLIPSIS_PAUSE_S = 0.42  # "..." is a held breath
MID_SENTENCE_PAUSE_S = 0.18  # a long sentence cut at a space
# Pieces are made this many ahead of the one being played, so the next is ready before it is needed.
# The first piece goes alone: nothing may slow down the first words.
PREFETCH = 2
# Around a sound or a silence written into the text: how long before it, and after it.
BEFORE_CUE_PAUSE_S = 0.20
AFTER_CUE_PAUSE_S = 0.16
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
    cue_started = Signal(str)  # a sound of a person (chuckle, hmm ...) is about to be heard: the face can join in

    def __init__(
        self,
        enabled: bool,
        tts: TextToSpeechService,
        playback: AudioPlaybackService,
        output_device: Callable[[], str | None],
        pool: QThreadPool,
        parent: QObject | None = None,
        cues: CueLibrary | None = None,
    ) -> None:
        super().__init__(parent)
        self._cues = cues
        self._language = "th"  # of the words being spoken, for the take of a cue
        self._tts = tts
        self._playback = playback
        self._output_device = output_device
        self._pool = pool
        self._status = SpeechStatus.DISABLED
        self._detail = ""
        # Bumped by every speak() and stop(); pieces carrying an older token are dropped.
        self._token = 0
        self._pieces: list[tuple[str, str]] = []  # (text, language)
        self._engine = ENGINE_LOCAL  # the voice for this whole answer
        self._launched = 0  # pieces handed to the thread pool so far
        self._next_chunk = 0  # the next piece to feed to the speaker
        self._made: dict[int, tuple[np.ndarray, str]] = {}  # finished pieces waiting for their turn
        self._fed_samples = 0
        self._began = 0.0  # when speak() was called
        self._first_fed = 0.0  # when the first piece reached the speaker
        self._fed_seconds = 0.0  # speech and pauses given to the speaker so far
        self._longest_wait = 0.0  # the longest time the speaker had nothing to play, between pieces
        self._speed: float | None = None
        self._speaking = False
        self._closing = False
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

    @property
    def can_choose_voices(self) -> bool:
        return hasattr(self._tts, "set_voices")

    @property
    def voice_names(self) -> tuple[str, str]:
        return self._tts.voice_names if hasattr(self._tts, "voice_names") else ("", "")

    def set_voices(self, thai: str | None = None, english: str | None = None) -> None:
        """Other online voices for what is spoken next."""
        if hasattr(self._tts, "set_voices"):
            self._tts.set_voices(thai, english)

    @property
    def engine_text(self) -> str:
        return getattr(self._tts, "engine_text", "")

    def speak(self, text: str, speed: float | None = None) -> bool:
        """Start speaking ``text``. Returns False if speech cannot be used right now.

        ``finished`` or ``failed`` follows, unless ``stop`` is called first.
        ``speed`` overrides the configured speed for this text only.
        """
        self._speed = speed
        self.stop()
        if not self.is_ready and not self._speaking_failed:
            return False
        self._engine = self._tts.plan_engine() if hasattr(self._tts, "plan_engine") else ENGINE_LOCAL
        pieces = speech_items(text, self._engine)
        if self._cues is None:  # no sounds to play: leave them out, and the pauses that went with them
            pieces = [piece for piece in pieces if piece[1] != LANG_CUE]
        spoken = [language for _value, language in pieces if language not in (LANG_CUE, LANG_PAUSE)]
        self._language = spoken[0] if spoken else "th"
        if not pieces or not spoken and not any(language == LANG_CUE for _v, language in pieces):
            return False
        device = self._output_device()
        logger.info(
            "Speaking %d characters (%d pieces, %s voice) through %s",
            len(text), len(pieces), self._engine, device or "the system's default speaker",
        )
        try:
            self._playback.start(self._tts.sample_rate, device)
        except PlaybackError as exc:
            logger.error("Cannot speak: %s", exc)
            self._mark_failed(str(exc))
            return False
        self._pieces = pieces
        self._launched = self._next_chunk = 0
        self._made = {}
        self._fed_samples = 0
        self._began = time.monotonic()
        self._first_fed = 0.0
        self._fed_seconds = 0.0
        self._longest_wait = 0.0
        self._speaking = True
        self._launch()
        self._poll_timer.start()
        return True

    def prerender(self, texts: list[str]) -> None:
        """Make the speech for ``texts`` now, in the background, so it plays at once later and without the network.

        Does nothing unless the online voice is in use. One piece at a time with a pause between, so the
        microphone and the speech models are never kept waiting.
        """
        if not getattr(self._tts, "online_available", False):
            return
        threading.Thread(target=self._prerender_now, args=(list(texts),), name="voice-prerender", daemon=True).start()

    def _prerender_now(self, texts: list[str]) -> None:
        """Make every piece of ``texts``; a piece the voice would not make is tried again a minute later (up to 6 rounds)."""
        todo = [(piece, language) for text in texts for piece, language in speech_items(text, ENGINE_EDGE)
                if language not in (LANG_CUE, LANG_PAUSE)]
        for round_ in range(6):
            todo = self._prerender_round(todo)
            if not todo or self._closing:
                break
            logger.info("%d pieces of speech not prepared yet: trying again in a minute", len(todo))
            time.sleep(60.0)

    def _prerender_round(self, pieces: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """Make ``pieces`` once through; returns the ones that failed (all of them if the online voice is not in use)."""
        made = 0
        failed: list[tuple[str, str]] = []
        for index, (piece, language) in enumerate(pieces):
            if self._closing or self._tts.plan_engine() != ENGINE_EDGE:
                logger.info("Prepared %d pieces of speech; the online voice is not available now", made)
                return pieces[index:] + failed
            try:
                if hasattr(self._tts, "prepare_piece"):
                    ok = self._tts.prepare_piece(piece, language)
                else:
                    self._tts.synthesize(piece, None, language, ENGINE_EDGE)
                    ok = True
            except Exception:  # noqa: BLE001 - this is only preparation
                logger.warning("Could not prepare a piece of speech", exc_info=True)
                ok = False
            if ok:
                made += 1
            else:
                failed.append((piece, language))
            time.sleep(0.4)
        logger.info("Prepared %d pieces of speech in advance (%d failed)", made, len(failed))
        return failed

    def close(self) -> None:
        """The program ends: nothing in the background may go on asking the online voice."""
        self._closing = True
        if hasattr(self._tts, "close"):
            self._tts.close()

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

    def _launch(self) -> None:
        """Start making pieces so that up to ``PREFETCH`` are in the making ahead of the next one to play."""
        if self._launched == 0:
            self._start_piece(0)
            if self._pieces[0][1] not in (LANG_CUE, LANG_PAUSE):
                return  # the first words go alone
        if self._next_chunk == 0 and 0 not in self._made:
            return
        while self._launched < len(self._pieces) and self._launched - self._next_chunk <= PREFETCH:
            self._start_piece(self._launched)

    def _start_piece(self, index: int) -> None:
        text, language = self._pieces[index]
        if language in (LANG_CUE, LANG_PAUSE):
            self._launched = max(self._launched, index + 1)
            self._made[index] = (self._sound(text, language), self._engine)  # nothing to wait for
            if index == self._next_chunk:
                self._feed_ready()
            return
        make = self._tts.synthesize_piece if hasattr(self._tts, "synthesize_piece") else self._make_plain
        first_words = all(kind in (LANG_CUE, LANG_PAUSE) for _value, kind in self._pieces[:index])
        worker = FunctionWorker(
            (self._token, index), make, text, self._speed, language,
            *((self._engine, first_words) if hasattr(self._tts, "synthesize_piece") else ()),
        )
        worker.signals.finished.connect(self._on_chunk_ready)
        worker.signals.failed.connect(self._on_chunk_failed)
        self._launched = max(self._launched, index + 1)
        self._pool.start(worker)

    def _sound(self, value: str, language: str) -> np.ndarray:
        """The audio of a cue or a pause piece (empty if the sound was never made)."""
        if language == LANG_PAUSE:
            return np.full(int(float(value) * self._tts.sample_rate), 1e-9, dtype=np.float32)  # silence that counts as audio
        take = self._cues.get(value, self._language) if self._cues is not None else None
        return take if take is not None else np.zeros(0, dtype=np.float32)

    def _make_plain(self, text: str, speed: float | None, language: str) -> tuple[np.ndarray, str]:
        return self._tts.synthesize(text, speed, language), ENGINE_LOCAL

    def _pause_before(self, index: int) -> float:
        """The silence before piece ``index``: how long depends on how the piece before it ended."""
        if index == 0:
            return 0.0
        previous_text, previous_voice = self._pieces[index - 1]
        current_voice = self._pieces[index][1]
        if LANG_PAUSE in (previous_voice, current_voice):
            return 0.0  # a written pause is the whole pause
        if current_voice == LANG_CUE:
            return BEFORE_CUE_PAUSE_S
        if previous_voice == LANG_CUE:
            return AFTER_CUE_PAUSE_S
        if current_voice != previous_voice:
            return VOICE_CHANGE_PAUSE_S
        ending = previous_text.rstrip()
        if ending.endswith(("...", "…")):
            return ELLIPSIS_PAUSE_S
        if ending.endswith("?"):
            return QUESTION_PAUSE_S
        if ending.endswith("!"):
            return EXCLAMATION_PAUSE_S
        if ending.endswith(_SENTENCE_ENDINGS):
            return PIECE_PAUSE_S
        return MID_SENTENCE_PAUSE_S

    @Slot(object, object)
    def _on_chunk_ready(self, token: tuple, result: object) -> None:
        if token[0] != self._token:
            return
        self._made[token[1]] = result  # (audio, the engine that made it)
        self._feed_ready()
        self._launch()

    def _feed_ready(self) -> None:
        """Give the speaker every piece that is ready and next in order."""
        while self._next_chunk in self._made:
            index = self._next_chunk
            audio, engine = self._made.pop(index)
            if engine != self._engine:
                logger.warning("The answer changes voice at piece %d: the %s voice failed", index, self._engine)
                self._engine = engine
            rate = self._tts.sample_rate
            audio = _trimmed(audio, rate)
            seconds = self._pause_before(index) if len(audio) else 0.0
            cue = self._pieces[index][0] if self._pieces[index][1] == LANG_CUE and len(audio) else None
            if len(audio):
                now = time.monotonic()
                if self._first_fed == 0.0:
                    self._first_fed = now
                    turn_timing.finish()  # the first words are on their way to the speaker
                else:
                    wait = now - (self._first_fed + self._fed_seconds)
                    if wait > 0.0:
                        self._longest_wait = max(self._longest_wait, wait)  # the speaker ran dry before this piece
                if cue is not None:  # the face joins in when the sound is heard, not when it is handed over
                    heard_in = (self._first_fed + self._fed_seconds + seconds) - now
                    QTimer.singleShot(
                        max(0, int(heard_in * 1000)), lambda name=cue, token=self._token: self._cue_heard(name, token))
                audio = np.concatenate((np.zeros(int(seconds * rate), dtype=np.float32), audio))
                self._playback.feed(audio, rate)
                self._fed_seconds += len(audio) / rate
                self._fed_samples += len(audio)
            self._next_chunk += 1
        if self._next_chunk < len(self._pieces):
            return
        if self._fed_samples == 0:
            self._fail(NO_AUDIO_MESSAGE)
            return
        self._playback.finish()
        logger.info(
            "Speech: %d pieces, %s voice, first sound %.2f s after the text, %.1f s of speech, longest wait between pieces %.2f s",
            len(self._pieces), self._engine, self._first_fed - self._began, self._fed_seconds, self._longest_wait,
        )

    def _cue_heard(self, name: str, token: int) -> None:
        if token == self._token and self._speaking:
            self.cue_started.emit(name)

    @Slot(object, str)
    def _on_chunk_failed(self, token: tuple, error: str) -> None:
        if token[0] != self._token:
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
