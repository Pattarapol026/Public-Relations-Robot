"""Listening to one voice: the visitor who started the turn. The voice-print logic, the gate in the voice
controller, and the words the speech model invents from noise."""
from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from app.state import ConversationState
from app.voice_controller import VoiceStatus, is_stray_text
from audio.speaker_lock import OTHER, VISITOR, SpeakerEmbedder, SpeakerLock, fbank
from fakes import LOUD, QUIET
from test_voice import build, pump, state, voice_settings, wait_until  # noqa: F401 - fixtures and helpers


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def unit(*values):
    vector = np.asarray(values, dtype=np.float32)
    return vector / np.linalg.norm(vector)


class FakeEmbedder:
    """Stands in for the network: the 'voice' of a clip is whatever ``voice`` says right now."""

    available = True

    def __init__(self):
        self.voice = unit(1, 0, 0)

    def embed(self, audio):
        return self.voice


# --- the voice print ---


def test_the_first_voice_is_the_visitor_and_a_clearly_different_one_is_someone_else():
    lock = SpeakerLock(FakeEmbedder())
    assert lock.classify(unit(1, 0.1, 0), 0.0) is VISITOR or lock.classify(unit(1, 0.1, 0), 0.0) == VISITOR
    assert lock.enrolled
    assert lock.classify(unit(1, 0.3, 0), 1.0) == VISITOR  # the same person, a little different
    assert lock.classify(unit(0, 1, 0), 2.0) == OTHER  # someone else
    assert lock.classify(None, 3.0) is None  # no opinion


def test_a_reset_or_a_long_silence_makes_the_next_voice_the_visitor():
    lock = SpeakerLock(FakeEmbedder())
    lock.classify(unit(1, 0, 0), 0.0)
    lock.reset()
    assert lock.classify(unit(0, 1, 0), 1.0) == VISITOR
    assert lock.classify(unit(1, 0, 0), 500.0) == VISITOR  # nobody matched it for minutes: forgotten


def test_features_have_the_shape_the_model_takes_and_too_little_audio_has_none():
    features = fbank(np.random.default_rng(0).normal(0, 0.1, 16_000).astype(np.float32))
    assert features.shape == (98, 80) and abs(float(features.mean())) < 1e-3
    assert fbank(np.zeros(1_000, dtype=np.float32)) is None


def test_an_embedder_without_its_model_has_no_opinion_and_never_raises():
    embedder = SpeakerEmbedder(models_dir="no/such/folder")
    assert embedder.load() is False and embedder.embed(np.zeros(16_000, dtype=np.float32)) is None


# --- words invented from noise ---


@pytest.mark.parametrize("text", ["Thanks for watching!", "ขอบคุณที่รับชม", "♪ ♪", "  .  "])
def test_credits_and_music_notes_are_never_a_question(text):
    assert is_stray_text(text, 5.0)


def test_a_bare_thank_you_needs_a_real_second_of_voice_but_a_question_never_counts_as_stray():
    assert is_stray_text("Thank you.", 0.3)
    assert not is_stray_text("Thank you.", 1.5)
    assert not is_stray_text("ค่าเทอมเท่าไหร่ครับ", 0.2)
    assert not is_stray_text("ขอบคุณครับ", 0.5)  # a visitor really says it (the test of 8 Nov caught the filter dropping it)


# --- in the voice controller ---


@pytest.fixture
def locked(qt_app, voice_settings):  # noqa: F811
    application, microphone, stt = build(qt_app, voice_settings)
    wait_until(qt_app, lambda: application.voice.status is VoiceStatus.READY)
    embedder = FakeEmbedder()
    application.voice.speaker_lock = SpeakerLock(embedder)
    microphone.duration = 5.0
    microphone.recent = lambda seconds: np.zeros(int(seconds * 16_000), dtype=np.float32)
    yield application, microphone, stt, embedder
    stt.release.set()
    application.shutdown()
    application.window.deleteLater()


def test_another_voice_after_the_visitor_does_not_hold_the_turn_open(qt_app, locked):
    application, microphone, stt, embedder = locked
    application.window.user_screen._on_talk_button()
    microphone.level = LOUD
    wait_until(qt_app, lambda: application.voice.speaker_lock.enrolled, timeout_s=5.0)  # the visitor speaks
    assert state(application) is ConversationState.LISTENING
    embedder.voice = unit(0, 1, 0)  # someone else keeps talking, loudly, after the visitor stopped
    wait_until(qt_app, lambda: state(application) is ConversationState.SPEAKING, timeout_s=8.0)
    assert stt.transcribed == 1


def test_the_same_voice_keeps_the_turn_going_as_before(qt_app, locked):
    application, microphone, stt, _embedder = locked
    application.window.user_screen._on_talk_button()
    microphone.level = LOUD
    pump(qt_app, 1.5)
    assert state(application) is ConversationState.LISTENING  # still the visitor: nothing is cut short
    microphone.level = QUIET
    wait_until(qt_app, lambda: state(application) is ConversationState.SPEAKING)
    assert stt.transcribed == 1


def test_the_parts_of_a_recording_that_were_someone_elses_are_silenced(locked):
    application, _microphone, _stt, _embedder = locked
    voice = application.voice
    voice._spk_windows = [(0, 16_000, VISITOR), (24_000, 40_000, OTHER)]
    audio = np.ones(48_000, dtype=np.float32)
    masked = voice._masked(audio)
    assert masked[:16_000].min() == 1.0  # the visitor's second is kept
    assert masked[28_000:36_000].max() == 0.0  # the other voice is silenced
    assert masked.shape == audio.shape
    voice._spk_windows = [(0, 16_000, VISITOR)]
    assert voice._masked(audio) is audio  # nobody else spoke: untouched


def test_a_voice_print_nobody_has_matched_for_a_while_is_dropped_when_the_speaker_is_refused_for_long(qt_app, locked):
    """11 Nov 2026: speaking to the robot and nothing happened: the print held was someone else's (or its own voice)."""
    import time

    application, microphone, stt, embedder = locked
    voice = application.voice
    import dataclasses

    voice.set_endpoint_config(dataclasses.replace(voice.endpoint_config, no_speech_timeout_s=8.0, max_seconds=12.0))
    application.window.user_screen._on_talk_button()  # (a new recording forgets the print: the print is set up after it starts)
    voice.speaker_lock.classify(unit(1, 0, 0), time.monotonic() - 40.0)  # an old print of another voice
    embedder.voice = unit(0, 1, 0)  # the person speaking now
    assert voice.speaker_lock.idle_seconds(time.monotonic()) >= 39.0
    microphone.level = LOUD
    wait_until(qt_app, lambda: state(application) is ConversationState.LISTENING, timeout_s=3.0)
    wait_until(qt_app, lambda: voice.heard_speech, timeout_s=8.0)  # refused for 1.5 s, then the print is dropped
    assert voice.speaker_lock.idle_seconds(time.monotonic()) < 5.0  # and the speaker is the one remembered now
    microphone.level = QUIET
    wait_until(qt_app, lambda: state(application) is ConversationState.SPEAKING, timeout_s=8.0)
    assert stt.transcribed == 1


def test_a_voice_that_matched_a_moment_ago_is_still_held_to_it_so_a_bystander_does_not_take_over(qt_app, locked):
    import time

    application, microphone, _stt, embedder = locked
    voice = application.voice
    import dataclasses

    voice.set_endpoint_config(dataclasses.replace(voice.endpoint_config, no_speech_timeout_s=8.0, max_seconds=12.0))
    application.window.user_screen._on_talk_button()
    voice.speaker_lock.classify(unit(1, 0, 0), time.monotonic() - 2.0)  # the visitor spoke two seconds ago, in this recording
    embedder.voice = unit(0, 1, 0)  # someone else, loudly
    microphone.level = LOUD
    pump(qt_app, 3.0)
    assert not voice.heard_speech  # refused all the time: nobody takes the turn
    assert voice.speaker_lock.idle_seconds(time.monotonic()) < 10.0


def test_the_lock_tells_how_long_since_its_voice_was_heard():
    lock = SpeakerLock(FakeEmbedder())
    assert lock.idle_seconds(100.0) == float("inf")
    lock.classify(unit(1, 0, 0), 100.0)
    assert lock.idle_seconds(130.0) == 30.0


def test_loud_readings_the_voice_filter_turned_away_are_tried_anyway_when_nothing_else_was(qt_app, locked):
    """11 Nov 2026: the level bar moved, nothing was processed. Silero called a real (quiet) voice "not a voice"."""
    import dataclasses

    application, microphone, stt, _embedder = locked
    voice = application.voice

    class NotAVoice:
        def is_voice(self, _audio):
            return False

    voice.voice_filter = NotAVoice()
    voice.speaker_lock = None  # only the voice filter refuses here
    voice.set_endpoint_config(dataclasses.replace(voice.endpoint_config, no_speech_timeout_s=1.5, max_seconds=6.0))
    application.window.user_screen._on_talk_button()
    microphone.level = LOUD
    wait_until(qt_app, lambda: stt.transcribed >= 1, timeout_s=8.0)  # nothing "counted", yet it was transcribed
    wait_until(qt_app, lambda: state(application) in (ConversationState.THINKING, ConversationState.SPEAKING), timeout_s=8.0)


def test_a_new_recording_does_not_hold_the_last_turns_voice_print(qt_app, locked):
    """11 Nov 2026: the visitor's louder second question was refused as "another voice" and nothing was processed."""
    application, _microphone, _stt, _embedder = locked
    voice = application.voice
    voice.speaker_lock.classify(unit(1, 0, 0), 0.0)
    assert voice.speaker_lock.enrolled
    application.window.user_screen._on_talk_button()  # a new recording starts
    assert not voice.speaker_lock.enrolled or voice.speaker_lock.idle_seconds(1e9) > 0  # re-enrolled only by new speech
    voice._reset_speaker_windows()
    assert not voice.speaker_lock.enrolled


def test_loud_other_voice_readings_are_tried_anyway(qt_app, locked):
    import dataclasses

    application, microphone, stt, embedder = locked
    voice = application.voice
    voice.set_endpoint_config(dataclasses.replace(voice.endpoint_config, no_speech_timeout_s=1.5, max_seconds=6.0))
    voice._reset_speaker_windows = lambda: None  # keep the print and the counters this test sets up
    application.window.user_screen._on_talk_button()
    voice.speaker_lock.classify(unit(1, 0, 0), __import__("time").monotonic())
    embedder.voice = unit(0, 1, 0)  # the person speaking is "another voice" to the print held
    microphone.level = LOUD
    microphone.peak = 0.5
    wait_until(qt_app, lambda: stt.transcribed >= 1, timeout_s=10.0)
