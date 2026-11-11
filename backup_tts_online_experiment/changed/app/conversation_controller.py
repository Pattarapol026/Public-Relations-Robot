"""Drives one conversation turn: question -> thinking -> answer -> speaking.

The robot speaks only an answer to something the visitor SAID, in a session they
opened with the "start" button. Everything else (typed questions, menu buttons,
greetings, announcements, a voice heard without that press) is shown on screen in
silence. Ending the session silences the speaker at once and voids all work still
in progress: every result carries the turn and session it belongs to.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from PySide6.QtCore import QObject, QThreadPool, QTimer, Signal, Slot

from app import turn_timing
from audio.text_to_speech import strip_markup
from services.expression import ExpressionPolicy
from app.speech_controller import SpeechController
from app.state import ConversationState, StateManager
from app.workers import FunctionWorker
from services.conversation_service import ConversationResult, ConversationService, Exchange

logger = logging.getLogger(__name__)

ERROR_MESSAGE = "ขออภัย ระบบตอบคำถามไม่ได้ในขณะนี้ กรุณาลองใหม่อีกครั้ง"
ERROR_DISPLAY_MS = 4000
MAX_HISTORY = 6  # exchanges remembered within one visitor's conversation

MODE_TEXT = "text"  # typed on the screen
MODE_VOICE = "voice"
MODE_SHORTCUT = "shortcut"  # a menu button

# (question, result, response time in ms, input mode) -> id of the stored record, if stored.
AnswerSink = Callable[[str, ConversationResult, int, str], "int | None"]
# (text, input mode) -> the robot's reply if the text was a robot command, else None.
CommandHandler = Callable[[str, str], "str | None"]


class ConversationController(QObject):
    question_received = Signal(str)
    answer_ready = Signal(object)  # ConversationResult
    reply_ready = Signal(str)  # the robot's reply to a command (not an answer from knowledge)
    error_occurred = Signal(str)
    cancelled = Signal()  # the current turn was abandoned (stop, new question, reset)
    # The robot has finished speaking (not cancelled). True after answering a
    # question, False after speaking first (a greeting).
    turn_finished = Signal(bool)

    def __init__(
        self,
        service: ConversationService,
        state: StateManager,
        pool: QThreadPool,
        speech: SpeechController | None = None,
        on_answer: AnswerSink | None = None,
        commands: CommandHandler | None = None,
        parent: QObject | None = None,
        expression: ExpressionPolicy | None = None,
    ) -> None:
        super().__init__(parent)
        self._commands = commands
        self._expression = expression  # which small human sound, if any, goes with an answer (services/expression.py)
        self._service = service
        self._state = state
        self._pool = pool
        self._speech = speech
        self._on_answer = on_answer or (lambda question, result, response_ms, mode: None)
        self._unrated_id: int | None = None
        self._history: list[Exchange] = []
        self._answering = False  # what the robot is saying now is an answer, not a greeting
        self._unrated_answer: ConversationResult | None = None
        # Bumped on every new question or cancel; results carrying an older
        # token belong to an abandoned turn and are dropped.
        self._token = 0
        self._session_id = 0
        self._session_active = False

        # Stands in for speech when it is off, unavailable or not allowed: the
        # answer stays "being read" for a time proportional to its length.
        self._speak_timer = QTimer(self)
        self._speak_timer.setSingleShot(True)
        self._speak_timer.timeout.connect(self._on_speaking_done)

        self._error_timer = QTimer(self)
        self._error_timer.setSingleShot(True)
        self._error_timer.timeout.connect(self._state.reset)

        if speech is not None:
            speech.finished.connect(self._on_speech_finished)
            speech.failed.connect(self._on_speech_failed)

    @property
    def state(self) -> StateManager:
        return self._state

    @property
    def answering(self) -> bool:
        """Working on or giving an answer to the visitor (not a greeting or a short reply)."""
        state = self._state.state
        return state is ConversationState.THINKING or (state is ConversationState.SPEAKING and self._answering)

    @property
    def session_active(self) -> bool:
        """The visitor pressed "start" and has not stopped or left since."""
        return self._session_active

    def begin_session(self) -> None:
        """The visitor pressed "start": their spoken questions are now answered aloud."""
        self.stop()
        self._session_id += 1
        self._session_active = True
        logger.info("Conversation session %d started", self._session_id)

    def end_session(self) -> None:
        """Stop, or the visitor has left the conversation: silence, and nothing comes later."""
        if self._session_active:
            logger.info("Conversation session %d ended", self._session_id)
        self._session_active = False
        self._session_id += 1  # a late result of the session just ended can never match
        self.stop()

    def start_listening(self) -> None:
        self.stop()
        self._state.transition(ConversationState.LISTENING)

    def begin_transcribing(self) -> None:
        """Listening has ended and the recording is being turned into text."""
        self._state.transition(ConversationState.THINKING)

    def stop(self) -> None:
        """Cancel the current turn, whatever stage it is in."""
        self._abandon_turn()
        self._state.reset()

    def forget(self) -> None:
        """Start a fresh conversation: the next visitor's questions stand alone."""
        self._history.clear()
        self._unrated_answer = None

    def take_unrated_answer(self) -> tuple[ConversationResult, int | None] | None:
        """The latest answer and its record id, if the visitor has not rated it yet.

        It then counts as rated.
        """
        answer, self._unrated_answer = self._unrated_answer, None
        return (answer, self._unrated_id) if answer is not None else None

    def fail(self, message: str) -> None:
        """Show ``message`` as the outcome of the current turn (e.g. an STT failure)."""
        self._abandon_turn()
        self._session_active = False
        if self._state.state is ConversationState.IDLE:
            self._state.transition(ConversationState.THINKING)
        self._state.transition(ConversationState.ERROR)
        self.error_occurred.emit(message)
        self._error_timer.start(ERROR_DISPLAY_MS)

    def say(self, text: str, aloud: bool = False) -> None:
        """The robot has something to show unasked: on screen, and aloud only if ``aloud``.

        Greetings are spoken (``aloud=True``); announcements, a farewell or a card's text are
        shown only. If the voice is not ready the text is shown for a reading pause instead.
        ``turn_finished`` follows when it has been spoken, or after the reading pause.
        """
        self.stop()
        self._answering = False
        self._state.transition(ConversationState.SPEAKING)
        self._speak(text, aloud=aloud)

    def reply_to_gesture(self, text: str) -> None:
        """The visitor showed a hand gesture: a short reply, aloud (unlike a card's text, it was asked for)."""
        self.say(text, aloud=True)

    def ask(self, text: str, mode: str = MODE_TEXT) -> None:
        text = text.strip()
        if not text:
            return
        self._abandon_turn()
        if self._state.state in (ConversationState.SPEAKING, ConversationState.ERROR):
            self._state.reset()
        self.question_received.emit(text)
        # From LISTENING (typed while listening) or THINKING (just transcribed)
        # this moves straight on without flashing through IDLE.
        self._state.transition(ConversationState.THINKING)

        # Robot commands are recognised here by fixed rules, before (and instead
        # of) asking the AI; menu buttons are always questions.
        reply = None
        if self._commands is not None and mode != MODE_SHORTCUT:
            reply = self._commands(text, mode)
        if reply is not None:
            self._unrated_answer = None  # a command is not an answer to rate
            self._answering = True  # so the robot listens again afterwards
            self._state.transition(ConversationState.SPEAKING)
            self.reply_ready.emit(reply)
            self._speak(reply, self._may_speak(mode))
            return

        worker = FunctionWorker(
            (self._token, self._session_id, text, mode, time.monotonic()),
            self._service.respond, text, tuple(self._history),
        )
        worker.signals.finished.connect(self._on_finished)
        worker.signals.failed.connect(self._on_failed)
        self._pool.start(worker)

    def _abandon_turn(self) -> None:
        self._token += 1
        self._speak_timer.stop()
        self._error_timer.stop()
        if self._speech is not None:
            self._speech.stop()
        self.cancelled.emit()

    def _may_speak(self, mode: str) -> bool:
        """Every answer to the visitor is spoken, typed or spoken (from 2026-11-06).

        Until then only answers to spoken questions in a session were. What is typed in the
        Admin (the AI test) never comes through here. The microphone still opens by itself only
        within a session (``_on_turn_finished``), so a typed answer is not heard back.
        """
        return True

    def _speak(self, text: str, aloud: bool) -> None:
        if aloud and self._speech is not None and self._speech.speak(text):
            return
        self._speak_timer.start(min(8000, max(1500, 60 * len(strip_markup(text)))))

    @Slot(object, object)
    def _on_finished(self, tag: tuple, result: object) -> None:
        token, session, question, mode, started = tag
        if token != self._token or session != self._session_id:
            return
        turn_timing.mark("answer")
        self._history.append(Exchange(question, result.answer_text, result.language))
        del self._history[:-MAX_HISTORY]
        response_ms = round((time.monotonic() - started) * 1000)
        self._unrated_id = self._on_answer(question, result, response_ms, mode)
        self._unrated_answer = result
        self._answering = True
        self._state.transition(ConversationState.SPEAKING)
        self.answer_ready.emit(result)
        spoken = result.answer_text
        if self._expression is not None:  # a chuckle, "hmm"...: only in what is SPOKEN, never in what is shown or remembered
            spoken = self._expression.decorate(question, spoken, result.source, result.language)
        self._speak(spoken, self._may_speak(mode))

    def _on_speech_finished(self) -> None:
        if self._state.state is ConversationState.SPEAKING:
            self._on_speaking_done()

    def _on_speech_failed(self, error: str) -> None:
        # The answer is already on screen; leave it there for a reading pause.
        logger.error("Speech failed mid-answer: %s", error)
        if self._state.state is ConversationState.SPEAKING:
            self._speak_timer.start(2500)

    def _on_speaking_done(self) -> None:
        self._state.reset()
        self.turn_finished.emit(self._answering)

    @Slot(object, str)
    def _on_failed(self, tag: tuple, error: str) -> None:
        if tag[0] != self._token or tag[1] != self._session_id:
            return
        logger.error("Conversation turn failed: %s", error)
        self._state.transition(ConversationState.ERROR)
        self.error_occurred.emit(ERROR_MESSAGE)
        self._error_timer.start(ERROR_DISPLAY_MS)
