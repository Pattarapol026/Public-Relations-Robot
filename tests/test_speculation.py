"""Hearing a visitor out: a pause starts the transcription, but only a longer silence ends the turn.

A pause in the middle of a sentence must not cut it off (what they said next would be lost),
and a visitor who really is done must not wait for the silence and then for the transcription
one after the other.
"""
from __future__ import annotations

import time

import pytest
from PySide6.QtWidgets import QApplication

from app.application import Application
from app.state import ConversationState
from app.voice_controller import VoiceStatus
from audio.endpointing import EndpointConfig, EndpointDecision, Endpointer
from config.settings import load_settings
from fakes import LOUD, QUIET, FakeMicrophone, FakeStt
from ui.fonts import apply_app_font
from ui.log_bridge import QtLogHandler

DT = 0.05
QUESTION = "สมัครเรียนได้ที่ไหน"


def feed(endpointer: Endpointer, level: float, seconds: float) -> list[EndpointDecision]:
    return [endpointer.update(level, DT) for _ in range(round(seconds / DT))]


# --- the end-of-speech rules ---


def config() -> EndpointConfig:
    return EndpointConfig(level_threshold=0.02, min_speech_s=0.25, silence_s=1.2, early_silence_s=0.6)


def test_a_pause_is_reported_once_and_the_longer_silence_ends_the_turn():
    endpointer = Endpointer(config())
    assert EndpointDecision.PAUSED not in feed(endpointer, LOUD, 1.0)
    decisions = feed(endpointer, QUIET, 1.3)
    assert decisions.count(EndpointDecision.PAUSED) == 1
    assert decisions.index(EndpointDecision.PAUSED) < decisions.index(EndpointDecision.FINISHED)
    assert decisions[-1] is EndpointDecision.FINISHED


def test_speaking_again_after_a_pause_is_reported_but_a_click_is_not():
    endpointer = Endpointer(config())
    feed(endpointer, LOUD, 1.0)
    assert EndpointDecision.PAUSED in feed(endpointer, QUIET, 0.7)
    assert EndpointDecision.RESUMED not in feed(endpointer, LOUD, 0.1)  # a click or a cough
    feed(endpointer, QUIET, 0.2)
    assert EndpointDecision.RESUMED in feed(endpointer, LOUD, 0.4)  # real speech
    # ... and a later pause is a new one (counted a little later than the first: see the next test).
    assert EndpointDecision.PAUSED in feed(endpointer, QUIET, 1.05)


def test_a_pause_that_was_not_the_end_makes_the_next_one_count_later():
    """Someone who pauses to think pauses again; working it out at every pause costs the computer seconds."""
    endpointer = Endpointer(config())
    feed(endpointer, LOUD, 1.0)
    assert EndpointDecision.PAUSED in feed(endpointer, QUIET, 0.7)  # the first pause counts at 0.6 s
    feed(endpointer, LOUD, 0.4)  # ... they go on

    def seconds_until_paused(quiet: float) -> float | None:
        decisions = feed(endpointer, QUIET, quiet)
        feed(endpointer, LOUD, 0.4)
        return (decisions.index(EndpointDecision.PAUSED) + 1) * DT if EndpointDecision.PAUSED in decisions else None

    second = seconds_until_paused(1.15)  # a pause that is not the end of the turn (1.2 s)
    assert second is not None and second > 0.9  # about 0.96 s, not 0.6 s
    third = seconds_until_paused(1.15)
    # Later again, but never past the end of the turn: a pause that came after it would never be reported.
    assert third is not None and second < third < 1.2


def test_without_an_early_pause_nothing_changes():
    for early in (0.0, 1.2, 2.0):  # off, or not shorter than the silence that ends the turn
        endpointer = Endpointer(EndpointConfig(level_threshold=0.02, silence_s=1.2, early_silence_s=early))
        feed(endpointer, LOUD, 1.0)
        decisions = feed(endpointer, QUIET, 1.4)
        assert EndpointDecision.PAUSED not in decisions and EndpointDecision.RESUMED not in decisions
        assert decisions[-1] is EndpointDecision.FINISHED


# --- the application ---


@pytest.fixture(scope="module")
def qt_app():
    qt_app = QApplication.instance() or QApplication([])
    apply_app_font(qt_app)
    return qt_app


def wait_until(qt_app, condition, timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not condition():
        assert time.monotonic() < deadline, "condition not reached in time"
        qt_app.processEvents()
        time.sleep(0.01)


def pump(qt_app, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.01)


class Visitor:
    """Talks into the fake microphone, and holds the transcription back until told."""

    def __init__(self, qt_app, tmp_path) -> None:
        self.qt_app = qt_app
        self.microphone, self.stt = FakeMicrophone(), FakeStt(QUESTION)
        settings = load_settings(environ={
            "DATA_DIR": str(tmp_path / "data"), "LOG_DIR": str(tmp_path / "logs"),
            "MEDIA_DIR": str(tmp_path / "media"), "STT_ENABLED": "true", "TTS_ENABLED": "false",
            "VISION_ENABLED": "false", "MIC_ARRAY_REQUIRED": "false",
        })
        self.app = Application(settings, QtLogHandler(), microphone=self.microphone, stt=self.stt)
        wait_until(qt_app, lambda: self.app.voice.status is VoiceStatus.READY)
        self.app.voice.set_endpoint_config(EndpointConfig(
            level_threshold=0.02, min_speech_s=0.25, silence_s=1.0, early_silence_s=0.3,
            no_speech_timeout_s=5.0, max_seconds=30.0,
        ))
        self.questions: list[str] = []
        self.app.conversation.question_received.connect(self.questions.append)
        self.screen = self.app.window.user_screen

    def close(self) -> None:
        self.stt.release.set()
        self.app.shutdown()
        self.app.window.deleteLater()

    @property
    def voice(self):
        return self.app.voice

    @property
    def state(self) -> ConversationState:
        return self.app.conversation.state.state

    def start(self) -> None:
        self.screen.show_page(self.screen.PAGE_CHAT)
        self.screen._on_talk_button()
        assert self.state is ConversationState.LISTENING

    def speak(self, seconds: float = 0.4) -> None:
        self.microphone.level = LOUD
        pump(self.qt_app, seconds)
        self.microphone.level = QUIET

    def pause(self, seconds: float) -> None:
        self.microphone.level = QUIET
        pump(self.qt_app, seconds)


@pytest.fixture
def visitor(qt_app, tmp_path):
    visitor = Visitor(qt_app, tmp_path)
    yield visitor
    visitor.close()


def test_the_answer_is_ready_when_the_silence_is_long_enough_not_after_it(qt_app, visitor):
    visitor.start()
    visitor.speak()
    visitor.pause(0.5)  # past the early pause (0.3 s), short of the silence that ends the turn (1.0 s)
    assert visitor.voice.speculating and visitor.microphone.snapshots == 1
    assert visitor.microphone.recording  # still listening
    wait_until(qt_app, lambda: visitor.questions == [QUESTION], timeout_s=4.0)
    assert visitor.stt.transcribed == 1  # one transcription, begun at the pause, not another at the end
    assert not visitor.microphone.recording


def test_while_the_transcription_is_still_running_the_robot_keeps_listening(qt_app, visitor):
    visitor.stt.release.clear()  # the transcription takes a long time
    visitor.start()
    visitor.speak()
    visitor.pause(1.6)  # well past the silence that ends the turn
    assert visitor.microphone.recording and visitor.state is ConversationState.LISTENING
    assert visitor.questions == []
    visitor.stt.release.set()
    wait_until(qt_app, lambda: visitor.questions == [QUESTION])
    assert not visitor.microphone.recording


def test_speaking_on_after_a_pause_drops_the_early_result_and_keeps_the_whole_sentence(qt_app, visitor):
    visitor.stt.release.clear()
    visitor.start()
    visitor.speak()
    visitor.pause(0.5)
    assert visitor.voice.speculating
    visitor.speak(0.5)  # "... and one more thing"
    assert not visitor.voice.speculating
    visitor.stt.release.set()  # the early transcription finishes late: it must be ignored
    pump(qt_app, 0.2)
    assert visitor.questions == [] and visitor.microphone.recording and visitor.state is ConversationState.LISTENING
    visitor.pause(0.8)  # a new pause (later than the first, which was not the end): everything said so far
    wait_until(qt_app, lambda: visitor.questions == [QUESTION], timeout_s=6.0)
    assert len(visitor.questions) == 1 and visitor.microphone.snapshots == 2
    assert visitor.stt.transcribed == 1  # the early job was dropped before it ran: it never held up the real one


def test_pressing_done_while_it_is_working_waits_for_that_result(qt_app, visitor):
    visitor.stt.release.clear()
    visitor.start()
    visitor.speak()
    visitor.pause(0.5)
    visitor.screen._on_talk_button()  # "พูดจบแล้ว"
    assert not visitor.microphone.recording and visitor.state is ConversationState.THINKING
    assert visitor.questions == []
    visitor.stt.release.set()
    wait_until(qt_app, lambda: visitor.questions == [QUESTION])
    assert visitor.stt.transcribed == 1  # the trial result was the answer; nothing was transcribed twice


def test_the_screen_says_it_is_working_during_the_pause(qt_app, visitor):
    visitor.stt.release.clear()
    visitor.start()
    visitor.speak()
    visitor.pause(0.5)
    assert "กำลังประมวลผล" in visitor.screen._state_label.text()
    assert visitor.screen.face_mood() == "thinking"
    visitor.speak(0.5)  # they went on
    assert "กำลังฟัง" in visitor.screen._state_label.text()


def test_stopping_during_the_pause_discards_the_trial_result(qt_app, visitor):
    visitor.stt.release.clear()
    visitor.start()
    visitor.speak()
    visitor.pause(0.5)
    visitor.app.conversation.end_session()  # the stop button
    visitor.stt.release.set()
    pump(qt_app, 0.5)
    assert visitor.questions == [] and not visitor.microphone.recording
