"""The voice filter: loud sounds that are not a voice neither start a turn nor hold one open."""
from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from app import turn_timing
from app.state import ConversationState
from app.voice_controller import VoiceStatus
from audio.voice_activity import CHUNK, VoiceActivityDetector
from fakes import LOUD, QUIET
from test_voice import build, pump, state, voice_settings, wait_until  # noqa: F401 - fixtures and helpers


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


class FakeFilter:
    def __init__(self, verdict):
        self.verdict = verdict

    def is_voice(self, audio):
        return self.verdict() if callable(self.verdict) else self.verdict


@pytest.fixture
def filtered(qt_app, voice_settings):  # noqa: F811
    application, microphone, stt = build(qt_app, voice_settings)
    wait_until(qt_app, lambda: application.voice.status is VoiceStatus.READY)
    yield application, microphone, stt
    stt.release.set()
    application.shutdown()
    application.window.deleteLater()


def test_a_loud_sound_that_is_not_a_voice_never_starts_a_turn(qt_app, filtered):
    application, microphone, stt = filtered
    application.voice.voice_filter = FakeFilter(False)
    application.window.user_screen._on_talk_button()
    microphone.level = LOUD  # a fan, loud enough to pass the level threshold
    wait_until(qt_app, lambda: state(application) is ConversationState.IDLE)  # nobody spoke: the turn times out
    assert stt.transcribed == 0


def test_noise_after_the_words_does_not_hold_the_turn_open(qt_app, filtered):
    application, microphone, stt = filtered
    speaking = {"now": True}
    application.voice.voice_filter = FakeFilter(lambda: speaking["now"])
    application.window.user_screen._on_talk_button()
    microphone.level = LOUD
    pump(qt_app, 0.5)  # the visitor speaks
    assert state(application) is ConversationState.LISTENING
    speaking["now"] = False  # they stop, but the level stays loud (steady noise)
    wait_until(qt_app, lambda: state(application) is ConversationState.SPEAKING)
    assert stt.transcribed == 1


def test_no_opinion_from_the_filter_means_the_level_decides_as_before(qt_app, filtered):
    application, microphone, stt = filtered
    application.voice.voice_filter = FakeFilter(None)
    application.window.user_screen._on_talk_button()
    microphone.level = LOUD
    pump(qt_app, 0.5)
    microphone.level = QUIET
    wait_until(qt_app, lambda: state(application) is ConversationState.SPEAKING)
    assert stt.transcribed == 1


# --- the detector itself ---


class FakeModel:
    def __init__(self, probabilities):
        self.probabilities = probabilities
        self.calls = []

    def __call__(self, audio):
        self.calls.append(len(audio))
        return np.asarray(self.probabilities[: len(audio) // CHUNK], dtype=np.float32)


def test_the_detector_says_voice_only_above_the_threshold_and_judges_the_last_half_second():
    detector = VoiceActivityDetector(lambda: FakeModel([0.1, 0.2, 0.9, 0.1] * 5))
    assert detector.load()
    audio = np.zeros(16_000, dtype=np.float32)
    assert detector.is_voice(audio) is True  # one chunk of 0.9 is enough: it is the highest in the window
    assert detector._model.calls[-1] <= 8000 and detector._model.calls[-1] % CHUNK == 0
    quiet = VoiceActivityDetector(lambda: FakeModel([0.1, 0.2, 0.3] * 8))
    quiet.load()
    assert quiet.is_voice(audio) is False


def test_too_little_audio_or_no_model_gives_no_opinion_and_a_broken_model_switches_itself_off():
    detector = VoiceActivityDetector(lambda: FakeModel([0.9] * 30))
    detector.load()
    assert detector.is_voice(np.zeros(500, dtype=np.float32)) is None  # too short to judge

    def no_model():
        raise RuntimeError("no onnx")

    missing = VoiceActivityDetector(no_model)
    assert missing.load() is False and missing.is_voice(np.zeros(8000, dtype=np.float32)) is None

    class Broken:
        def __call__(self, audio):
            raise RuntimeError("boom")

    broken = VoiceActivityDetector(Broken)
    broken.load()
    assert broken.is_voice(np.zeros(8000, dtype=np.float32)) is None and not broken.available


# --- the timing line ---


def test_a_turn_logs_where_its_time_went(caplog):
    caplog.set_level("INFO", logger="app.turn_timing")
    turn_timing._marks.clear()  # other tests' turns leave stages behind
    turn_timing.mark("transcript", 10.9)  # the trial transcription is ready before the end is decided
    turn_timing.begin(10.0, 11.0)
    turn_timing.mark("answer", 12.5)
    turn_timing.finish(13.0)
    line = next(r.getMessage() for r in caplog.records if "Turn timing" in r.getMessage())
    assert "waited 1.00 s" in line and "transcript +0.90 s" in line and "answer +2.50 s" in line
    assert "first sound +3.00 s" in line
    caplog.clear()
    turn_timing.finish()  # nothing is being timed now
    assert not caplog.records
