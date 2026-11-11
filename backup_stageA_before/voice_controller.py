"""Voice input: microphone -> end-of-speech detection -> speech-to-text -> question.

Everything slow (model loading, transcription) runs in the thread pool. The
microphone is polled from a GUI-thread timer, so no Qt call is ever made from
the audio driver's thread.

Ending a turn waits to be sure the visitor is done, without making them wait for it. At a
short pause (``EndpointConfig.early_silence_s``) what has been said so far is transcribed in the
background while the microphone stays open. If they go on speaking, that result is thrown away
and listening continues, so a pause in the middle of a sentence no longer cuts it off. If they
stay silent, the answer is ready at the moment the longer silence confirms they are done: the
silence and the transcription overlap instead of one following the other.
"""
from __future__ import annotations

import logging
import time
import unicodedata
from enum import Enum
from typing import Callable

from PySide6.QtCore import QObject, QThreadPool, QTimer, Signal, Slot

from app.conversation_controller import MODE_VOICE, ConversationController
from app.workers import FunctionWorker
from app import turn_timing
from audio.endpointing import EndpointConfig, EndpointDecision, Endpointer
from audio.microphone import AudioDevice, MicrophoneError, MicrophoneService
import numpy as np

from audio.speaker_lock import OTHER, VISITOR, SpeakerEmbedder, SpeakerLock, download_model, find_model_file
from audio.speech_to_text import SpeechToTextService

logger = logging.getLogger(__name__)

VOICE_CHECK_EVERY_S = 0.1
SPEAKER_WINDOW_S = 1.0  # how much audio one voice print is made from
SPEAKER_EVERY_S = 0.4  # how often a new window is checked while someone is speaking
SPEAKER_FRESH_S = 0.9  # a verdict older than this says nothing about what is being heard now
SAMPLE_RATE = 16_000

# What Whisper "hears" in noise, music or silence: credits and thanks that nobody said. Matched on the
# whole transcript, lower case, without punctuation.
STRAY_PHRASES = frozenset({
    "thanks for watching", "thank you for watching", "thank you so much for watching", "please subscribe",
    "subscribe", "like and subscribe", "see you next time", "see you in the next video", "subtitles by",
    "ขอบคุณที่รับชม", "ขอบคุณที่ติดตามรับชม", "ขอบคุณที่รับชมนะคะ", "ขอบคุณที่รับชมนะครับ",
    "อย่าลืมกดติดตาม", "อย่าลืมกดไลค์กดแชร์", "กดติดตามช่องด้วยนะคะ", "ติดตามได้ที่",
})
# Short words Whisper invents from a click or a hum; real only if there was a real second of speech.
STRAY_IF_BRIEF = frozenset({"thank you", "thanks", "you", "bye", "bye bye", "ok", "okay", "the end"})
# (Thai "ขอบคุณครับ" is left out on purpose: a visitor really says it, and it was never what the model invented.)
BRIEF_VOICED_S = 0.9


def is_stray_text(text: str, voiced_s: float) -> bool:
    """A transcript that is a known invention of the speech model, or a stray word from far too little voice."""
    # Punctuation and symbols go; Thai vowel and tone marks are not alphanumeric but must stay.
    words = "".join(ch for ch in text.lower() if unicodedata.category(ch)[0] not in "PSC")
    words = " ".join(words.split())
    if not words or words in STRAY_PHRASES or "\u266a" in text:
        return True
    return words in STRAY_IF_BRIEF and voiced_s < BRIEF_VOICED_S

POLL_MS = 50
MIC_TEST_MS = 5000
NO_SPEECH_MESSAGE = "ไม่ได้ยินเสียงพูด กรุณากด \"เริ่มสนทนา\" แล้วพูดอีกครั้ง"
DEAD_MICROPHONE_MESSAGE = (
    "ไมโครโฟนไม่มีสัญญาณเสียงเลย กรุณาแจ้งเจ้าหน้าที่ "
    "(ไมโครโฟนอาจถูกปิดเสียง หรือระบบไม่อนุญาตให้โปรแกรมใช้ไมโครโฟน)"
)
STT_FAILED_MESSAGE = "ถอดเสียงเป็นข้อความไม่สำเร็จ กรุณาลองใหม่หรือพิมพ์คำถามแทน"


class VoiceStatus(Enum):
    DISABLED = "disabled"
    LOADING = "loading"
    READY = "ready"
    UNAVAILABLE = "unavailable"


_STATUS_TEXT = {
    VoiceStatus.DISABLED: "ปิดใช้งาน",
    VoiceStatus.LOADING: "กำลังเตรียม...",
    VoiceStatus.READY: "พร้อม",
    VoiceStatus.UNAVAILABLE: "ใช้งานไม่ได้",
}
CHECKING_MICROPHONE = "กำลังตรวจสอบไมโครโฟน"


class VoiceController(QObject):
    status_changed = Signal()
    level_changed = Signal(float)  # 0.0–1.0 RMS while listening or testing
    notice = Signal(str, str)  # message, "info" | "error"
    mic_test_finished = Signal()
    speculation_changed = Signal()  # a trial transcription started or ended: "heard you, working on it"

    def __init__(
        self,
        enabled: bool,
        microphone: MicrophoneService,
        stt: SpeechToTextService,
        conversation: ConversationController,
        endpoint_config: EndpointConfig,
        device_name: Callable[[], str | None],
        pool: QThreadPool,
        channel: Callable[[], int] = lambda: 0,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._channel = channel
        self._language: str | None = None  # None = the configured language
        self._microphone = microphone
        self._stt = stt
        self._conversation = conversation
        self._endpoint_config = endpoint_config
        self._endpointer = Endpointer(endpoint_config)
        self._device_name = device_name
        self._pool = pool
        self._status = VoiceStatus.DISABLED
        self._detail = ""
        self._listening = False
        self._starting = False
        self._follow_up = False
        self._testing = False
        self._token = 0
        self._last_poll = 0.0
        self._model_ready = False
        self._checking = False
        self._check_again = False
        # Set by the app when a direction-capable microphone is in use: () -> False when the
        # utterance confidently did not come from the visitor (None/True = answer as usual).
        self.speaker_check: Callable[[], bool | None] | None = None
        # Set by the app when listening by direction is switched on: () -> True while the board hears
        # a voice from outside the visitor's direction. It only helps a turn that has begun to END
        # (loud room, visitor stopped): it never stops a turn from starting.
        self.direction_gate: Callable[[], bool] | None = None
        self._direction_gated = 0
        # Set when the program has a voice detector: a loud sound it says is not speech neither starts
        # a turn nor holds one open. Without it (tests, or it could not be loaded) the level alone decides.
        self.voice_filter = None
        self._noise_gated = 0
        self._vad_checked_at = 0.0
        self._vad_is_voice = True
        # Called when a turn starts listening, so direction can note who is speaking (see DirectionController).
        self.turn_started: Callable[[], None] | None = None
        self._from_visitor: bool | None = None
        # Listening to ONE voice: the first clear second of speech is taken to be the visitor's, and
        # windows that match it less well than 0.35 are someone else (see audio/speaker_lock.py).
        self.speaker_lock: SpeakerLock | None = None
        self._speaker_models_dir = None
        self._spk_busy = False
        self._spk_last = 0.0
        self._spk_token = 0
        self._spk_latest: tuple[str, float] | None = None  # (VISITOR | OTHER, when)
        self._spk_windows: list[tuple[int, int, str]] = []  # (first sample, last sample, label) of this recording
        self._spk_ignored = 0
        self._voiced_s = 0.0  # seconds of this turn that were loud and sounded like a voice
        # The trial transcription of what was said before a pause. "none", "running" or "done".
        self._spec_state = "none"
        self._spec_token = 0
        self._spec_text = ""
        self._awaiting_spec = False  # listening has ended; the trial result is the answer to wait for

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(POLL_MS)
        self._poll_timer.timeout.connect(self._poll)
        self._test_timer = QTimer(self)
        self._test_timer.setSingleShot(True)
        self._test_timer.timeout.connect(self.stop_mic_test)

        conversation.cancelled.connect(self._on_turn_cancelled)
        if enabled:
            self._preload_model()

    # --- status ---

    @property
    def status(self) -> VoiceStatus:
        return self._status

    @property
    def status_text(self) -> str:
        text = _STATUS_TEXT[self._status]
        return f"{text} — {self._detail}" if self._detail else text

    @property
    def speculating(self) -> bool:
        """Listening, and already working out what was said before the pause that is going on."""
        return self._listening and self._spec_state != "none"

    @property
    def is_listening(self) -> bool:
        return self._listening

    @property
    def heard_speech(self) -> bool:
        """Listening, and someone has already spoken in this recording (so it is not just waiting)."""
        return self._listening and self._endpointer.heard_speech

    @property
    def speech_active(self) -> bool:
        """Listening, and the microphone level counts as speech right now (the endpointer's rule)."""
        return self._listening and self._microphone.level >= self._endpointer.threshold

    @property
    def language(self) -> str | None:
        return self._language

    def set_language(self, language: str | None) -> None:
        """Language of the visitor's speech for the next turns; None = the configured one."""
        self._language = language

    @property
    def endpoint_config(self) -> EndpointConfig:
        return self._endpoint_config

    def set_endpoint_config(self, config: EndpointConfig) -> None:
        """New end-of-speech rules; they apply from the next time listening starts."""
        self._endpoint_config = config

    @property
    def engine_text(self) -> str:
        """The speech models and where they run (graphics card or processor), for the Admin."""
        text = getattr(self._stt, "engine_text", "")
        return text or "faster-whisper"

    def input_devices(self) -> list[AudioDevice]:
        """Raises ``MicrophoneError`` if the audio system cannot be queried."""
        return self._microphone.list_input_devices()

    # --- listening ---

    def start_listening(self, follow_up: bool = False) -> bool:
        """Begin a voice turn. Returns False if voice input cannot be used right now.

        ``follow_up`` marks listening that the robot started by itself after an
        answer. If the visitor then says nothing, the turn ends quietly instead
        of asking them to try again.
        """
        if self._listening:
            return True  # already open; a second press or trigger must not reopen it
        if not self._model_ready or self._checking:
            return False
        # UNAVAILABLE with the model loaded means the microphone failed its
        # check: try again, it may have been plugged in or allowed since.
        self._follow_up = follow_up
        self.stop_mic_test()
        self._conversation.stop()  # cancel any turn in progress
        try:
            self._microphone.start(self._device_name(), self._channel())
        except MicrophoneError as exc:
            self._set_status(VoiceStatus.UNAVAILABLE, str(exc))
            if not follow_up:
                self._conversation.fail(str(exc))
            return False
        if self._status is not VoiceStatus.READY:
            self._set_status(VoiceStatus.READY, self._microphone_detail())
        self._endpointer = Endpointer(self._endpoint_config)
        self._direction_gated = 0
        self._noise_gated = 0
        self._vad_is_voice = True
        self._reset_speaker_windows()
        if self.turn_started is not None:
            self.turn_started()
        self._reset_speculation()
        self._listening = True
        self._last_poll = time.monotonic()
        self._poll_timer.start()
        # The microphone is already open when the state changes, so listeners
        # of the state change see is_listening == True. start_listening()
        # announces a cancel of the previous turn, which must not stop this one.
        self._starting = True
        try:
            self._conversation.start_listening()
        finally:
            self._starting = False
        return True

    def finish_listening(self) -> None:
        """Stop recording now and transcribe what was said (or use the trial transcription of it)."""
        if not self._listening:
            return
        if self._spec_state != "none":
            self._finish_with_speculation()
            return
        self._listening = False
        self._poll_timer.stop()
        self.level_changed.emit(0.0)
        audio = self._masked(self._microphone.stop())
        # Decided now, from the direction of the voice that has just ended.
        self._from_visitor = self.speaker_check() if self.speaker_check is not None else None
        self._conversation.begin_transcribing()

        self._token += 1
        token = self._token
        worker = FunctionWorker(
            token, self._stt.transcribe, audio, self._language, lambda: token != self._token
        )
        worker.signals.finished.connect(self._on_transcribed)
        worker.signals.failed.connect(self._on_transcribe_failed)
        self._pool.start(worker)

    # --- trial transcription while a pause goes on ---

    def _start_speculation(self) -> None:
        audio = self._masked(self._microphone.snapshot())
        self._spec_token += 1
        self._spec_state, self._spec_text = "running", ""
        token = self._spec_token
        worker = FunctionWorker(
            token, self._stt.transcribe, audio, self._language, lambda: token != self._spec_token
        )
        worker.signals.finished.connect(self._on_spec_result)
        worker.signals.failed.connect(self._on_spec_failed)
        self._pool.start(worker)
        self.speculation_changed.emit()

    def _reset_speculation(self) -> None:
        """Forget the trial transcription (speech went on, or the turn ended): a late result is ignored."""
        self._spec_token += 1
        self._awaiting_spec = False
        if self._spec_state != "none":
            self._spec_state, self._spec_text = "none", ""
            self.speculation_changed.emit()

    def _finish_with_speculation(self) -> None:
        """Listening is over: use the trial result if it is ready, else wait for it (no new transcription)."""
        self._listening = False
        self._poll_timer.stop()
        self.level_changed.emit(0.0)
        self._microphone.stop()  # the audio is already being transcribed
        self._from_visitor = self.speaker_check() if self.speaker_check is not None else None
        self._conversation.begin_transcribing()
        if self._spec_state == "done":
            self._deliver_speculation()
        else:
            self._awaiting_spec = True

    def _deliver_speculation(self) -> None:
        text = self._spec_text
        self._reset_speculation()
        self._token += 1
        self._on_transcribed(self._token, text)

    @Slot(object, object)
    def _on_spec_result(self, token: int, text: object) -> None:
        if token != self._spec_token:
            return  # speech went on after this was started, or the turn ended
        self._spec_state, self._spec_text = "done", str(text or "")
        turn_timing.mark("transcript")
        if self._awaiting_spec:
            self._deliver_speculation()
        else:
            self.speculation_changed.emit()

    @Slot(object, str)
    def _on_spec_failed(self, token: int, _error: str) -> None:
        if token != self._spec_token:
            return
        awaiting = self._awaiting_spec
        self._reset_speculation()
        if awaiting:
            self._conversation.fail(STT_FAILED_MESSAGE)
        # Still listening: the normal transcription at the end of the turn tries again.

    def shutdown(self) -> None:
        self._token += 1
        self._spec_token += 1
        self._stop_capture()

    # --- microphone test (Admin) ---

    def start_mic_test(self, device_name: str | None, channel: int = 0) -> None:
        """Show live input level for a few seconds. Raises ``MicrophoneError``."""
        if self._listening:
            raise MicrophoneError("กำลังใช้ไมโครโฟนรับเสียงสนทนาอยู่")
        self.stop_mic_test()
        self._microphone.start(device_name, channel, fallback=False)
        self._testing = True
        self._poll_timer.start()
        self._test_timer.start(MIC_TEST_MS)

    def stop_mic_test(self) -> None:
        if not self._testing:
            return
        self._stop_capture()
        self.mic_test_finished.emit()

    # --- internals ---

    def _preload_model(self) -> None:
        self._set_status(VoiceStatus.LOADING)
        worker = FunctionWorker(None, self._stt.load)
        worker.signals.finished.connect(self._on_model_loaded)
        worker.signals.failed.connect(self._on_model_failed)
        self._pool.start(worker)

    @Slot(object, object)
    def _on_model_loaded(self, _token: object, _result: object) -> None:
        self._model_ready = True
        self.recheck_microphone()

    @Slot(object, str)
    def _on_model_failed(self, _token: object, error: str) -> None:
        # SpeechToTextError texts are written for the screen; the worker logged the rest.
        self._set_status(VoiceStatus.UNAVAILABLE, error)

    def recheck_microphone(self) -> None:
        """Prove the microphone delivers audio before calling voice input ready.

        Runs in the background; also used after the Admin changes the device.
        """
        if self._checking:
            self._check_again = True  # the device may have changed during this check
            return
        if not self._model_ready or self._listening:
            return
        self._checking = True
        self._check_again = False
        self._set_status(VoiceStatus.LOADING, CHECKING_MICROPHONE)
        worker = FunctionWorker(None, self._microphone.probe, self._device_name(), self._channel())
        worker.signals.finished.connect(self._on_microphone_checked)
        worker.signals.failed.connect(self._on_microphone_failed)
        self._pool.start(worker)

    @Slot(object, object)
    def _on_microphone_checked(self, _token: object, _label: object) -> None:
        self._checking = False
        self._set_status(VoiceStatus.READY, self._microphone_detail())
        if self._check_again:
            self.recheck_microphone()

    @Slot(object, str)
    def _on_microphone_failed(self, _token: object, error: str) -> None:
        # MicrophoneError texts are written for the screen.
        self._checking = False
        self._set_status(VoiceStatus.UNAVAILABLE, error)
        if self._check_again:
            self.recheck_microphone()

    def _microphone_detail(self) -> str:
        label = getattr(self._microphone, "device_label", "")
        return f"ไมโครโฟน: {label}" if label else ""

    def _set_status(self, status: VoiceStatus, detail: str = "") -> None:
        self._status, self._detail = status, detail
        logger.info("Voice input status: %s %s", status.value, detail)
        self.status_changed.emit()

    def _poll(self) -> None:
        now = time.monotonic()
        level = self._microphone.level
        self.level_changed.emit(level)
        if not self._listening:
            return
        if level >= self._endpointer.threshold and self.voice_filter is not None and not self._sounds_like_voice(now):
            level = 0.0  # loud, but not a voice (a fan, a door, a click): it starts nothing and holds nothing open
            self._noise_gated += 1
        if (self.direction_gate is not None and self._endpointer.heard_speech
                and level >= self._endpointer.threshold and self.direction_gate()):
            level = 0.0  # loud, but from someone else: it does not hold the visitor's turn open
            self._direction_gated += 1
        if level >= self._endpointer.threshold:
            self._voiced_s += now - self._last_poll
            if self.speaker_lock is not None and self.speaker_lock.available:
                self._check_speaker(now)
                if self._speaker_is_someone_else(now):
                    level = 0.0  # a voice, but not the visitor's: it starts nothing and holds nothing open
                    self._spk_ignored += 1
        decision = self._endpointer.update(level, now - self._last_poll)
        self._last_poll = now
        if decision is EndpointDecision.PAUSED:
            self._start_speculation()
        elif decision is EndpointDecision.RESUMED:
            self._reset_speculation()  # they were not done: keep listening, nothing is lost
        elif decision is EndpointDecision.FINISHED:
            if self._spec_state == "running":
                return  # the silence is long enough, but the answer to it is not ready: still listening
            if self._direction_gated:
                logger.info("Turn ended by direction: %d readings from another direction ignored",
                            self._direction_gated)
            if self._noise_gated:
                logger.info("Turn: %d loud readings were not a voice and did not count", self._noise_gated)
            if self._spk_ignored:
                logger.info("Turn: %d readings of another person's voice did not count", self._spk_ignored)
            turn_timing.begin(now - self._endpointer.silence_seconds, now)
            self.finish_listening()
        elif decision is EndpointDecision.TOO_LONG:
            turn_timing.begin(now - self._endpointer.silence_seconds, now)
            self.finish_listening()
        elif decision is EndpointDecision.NO_SPEECH:
            logger.info(
                "No speech heard: loudest input %.4f, speech counted above %.4f",
                self._microphone.peak, self._endpointer.threshold,
            )
            dead = self._microphone.peak == 0.0
            quiet = self._follow_up
            if quiet and not dead:
                # Always listening: nobody has spoken yet, so keep the microphone open
                # on a fresh recording instead of closing and reopening it.
                self._endpointer = Endpointer(self._endpoint_config)
                self._noise_gated = self._direction_gated = 0
                self._reset_speaker_windows()
                if self.turn_started is not None:
                    self.turn_started()  # a fresh recording: whoever speaks next is the visitor
                self._microphone.cancel()
                try:
                    self._microphone.start(self._device_name(), self._channel())
                    self._last_poll = time.monotonic()
                    return
                except MicrophoneError as exc:
                    logger.warning("Could not reopen the microphone: %s", exc)
            self._conversation.end_session()  # cancels capture through _on_turn_cancelled
            if quiet and not dead:
                return  # the visitor simply had nothing more to ask
            if dead:
                logger.warning("Microphone delivered only digital silence")
                self.notice.emit(DEAD_MICROPHONE_MESSAGE, "error")
            else:
                self.notice.emit(NO_SPEECH_MESSAGE, "info")

    # --- one voice only ---

    def prepare_speaker_lock(self, models_dir) -> None:
        """Load the speaker model (fetching it once, 26 MB, if it is not in the models folder). In the background."""
        if find_model_file(models_dir) is not None:
            embedder = SpeakerEmbedder(models_dir=models_dir)
            if embedder.load():
                self.speaker_lock = SpeakerLock(embedder)
                logger.info("Speaker lock on: the robot listens to the voice that starts the turn")
            return
        self._speaker_models_dir = models_dir
        worker = FunctionWorker(None, download_model, models_dir)
        worker.signals.finished.connect(self._on_speaker_model)
        self._pool.start(worker)

    @Slot(object, object)
    def _on_speaker_model(self, _token: object, path: object) -> None:
        if path:
            self.prepare_speaker_lock(self._speaker_models_dir)
        else:
            logger.warning("Speaker lock off: the speaker model is not available")

    def reset_speaker(self, *_args) -> None:
        """A new visitor (or none): the next clear voice is the one to listen to."""
        if self.speaker_lock is not None:
            self.speaker_lock.reset()

    def _reset_speaker_windows(self) -> None:
        self._spk_windows = []
        self._spk_latest = None
        self._spk_ignored = 0
        self._spk_busy = False
        self._spk_token += 1
        self._voiced_s = 0.0

    def _check_speaker(self, now: float) -> None:
        """While someone speaks: have the last second of audio compared with the visitor's voice, in the background."""
        duration = getattr(self._microphone, "duration", 0.0)
        if self._spk_busy or now - self._spk_last < SPEAKER_EVERY_S or duration < SPEAKER_WINDOW_S:
            return
        if self.voice_filter is not None and not self._vad_is_voice:
            return  # not a voice just now
        if self._voiced_s < SPEAKER_WINDOW_S * 0.8 and not self.speaker_lock.enrolled:
            return  # the first second of the turn must be mostly speech to be taken as the visitor
        self._spk_busy = True
        self._spk_last = now
        end = int(duration * SAMPLE_RATE)
        audio = self._microphone.recent(SPEAKER_WINDOW_S)
        token = self._spk_token
        worker = FunctionWorker(token, self._embed_window, audio, end)
        worker.signals.finished.connect(self._on_speaker_window)
        worker.signals.failed.connect(self._on_speaker_failed)
        self._pool.start(worker)

    def _embed_window(self, audio: np.ndarray, end: int) -> tuple[object, int]:
        return self.speaker_lock.embedder.embed(audio), end

    @Slot(object, str)
    def _on_speaker_failed(self, token: int, _error: str) -> None:
        if token == self._spk_token:
            self._spk_busy = False

    @Slot(object, object)
    def _on_speaker_window(self, token: int, result: object) -> None:
        if token != self._spk_token:
            return  # a window of a recording that is over
        self._spk_busy = False
        embedding, end = result
        if self.speaker_lock is None:
            return
        now = time.monotonic()
        label = self.speaker_lock.classify(embedding, now)
        if label is None:
            return
        self._spk_latest = (label, now)
        self._spk_windows.append((max(0, end - int(SPEAKER_WINDOW_S * SAMPLE_RATE)), end, label))

    def _speaker_is_someone_else(self, now: float) -> bool:
        latest = self._spk_latest
        return latest is not None and latest[0] == OTHER and now - latest[1] <= SPEAKER_FRESH_S

    def _masked(self, audio: np.ndarray) -> np.ndarray:
        """The recording with the windows that were someone else's silenced, so they are not transcribed.

        The middle half second of a window that matched the visitor stays whatever its neighbours say.
        """
        others = [(a, b) for a, b, label in self._spk_windows if label == OTHER]
        if not others or len(audio) == 0:
            return audio
        keep = np.ones(len(audio), dtype=bool)
        for a, b in others:
            keep[a:b] = False
        for a, b, label in self._spk_windows:
            if label == VISITOR:
                middle = (b - a) // 4
                keep[a + middle:b - middle] = True
        masked = np.where(keep, audio, 0.0).astype(np.float32)
        logger.info("Turn: %.1f s of another voice silenced before transcription", (~keep).sum() / SAMPLE_RATE)
        return masked

    def _sounds_like_voice(self, now: float) -> bool:
        """Whether the last half second holds a human voice. Judged at most every 0.1 s; no opinion means yes."""
        if now - self._vad_checked_at >= VOICE_CHECK_EVERY_S:
            self._vad_checked_at = now
            verdict = self.voice_filter.is_voice(self._microphone.recent(0.5))
            self._vad_is_voice = True if verdict is None else verdict
        return self._vad_is_voice

    def _on_turn_cancelled(self) -> None:
        if self._starting:
            return
        self._token += 1  # drop a transcription that is still running
        self._reset_speculation()
        if self._listening:
            self._stop_capture()

    def _stop_capture(self) -> None:
        self._listening = False
        self._testing = False
        self._poll_timer.stop()
        self._test_timer.stop()
        self._microphone.cancel()
        self.level_changed.emit(0.0)

    @Slot(object, object)
    def _on_transcribed(self, token: int, text: object) -> None:
        if token != self._token:
            return
        turn_timing.mark("transcript")
        if text and self._from_visitor is False:
            # Someone else in the room (or a voice where nobody stands): not a question to answer.
            logger.info("Utterance ignored: the voice did not come from the visitor (%d characters)", len(text))
            self._conversation.end_session()
            return
        if text and is_stray_text(str(text), self._voiced_s):
            logger.info("Utterance ignored: %r is what the speech model makes of noise (%.1f s of voice)", text, self._voiced_s)
            text = ""
        if not text:
            quiet = self._follow_up
            self._conversation.end_session()
            if not quiet:
                self.notice.emit(NO_SPEECH_MESSAGE, "info")
            return
        self._conversation.ask(text, MODE_VOICE)

    @Slot(object, str)
    def _on_transcribe_failed(self, token: int, _error: str) -> None:
        if token != self._token:
            return
        self._conversation.fail(STT_FAILED_MESSAGE)
