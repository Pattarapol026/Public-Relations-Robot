"""Voice turns through the real application, with a scripted microphone and STT."""
from __future__ import annotations

import threading
import time

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from app.application import Application
from app.state import ConversationState
from app.voice_controller import (
    DEAD_MICROPHONE_MESSAGE,
    NO_SPEECH_MESSAGE,
    STT_FAILED_MESSAGE,
    VoiceStatus,
)
from audio.microphone import AudioDevice, MicrophoneError
from audio.speech_to_text import SpeechToTextError
from config.settings import load_settings
from fakes import LOUD, QUIET, FakeMicrophone, FakeStt
from ui.fonts import apply_app_font
from ui.log_bridge import QtLogHandler

@pytest.fixture(scope="module")
def qt_app():
    qt_app = QApplication.instance() or QApplication([])
    apply_app_font(qt_app)
    return qt_app


@pytest.fixture
def voice_settings(tmp_path):
    return load_settings(
        environ={
            "DATA_DIR": str(tmp_path / "data"),
            "LOG_DIR": str(tmp_path / "logs"),
            "MEDIA_DIR": str(tmp_path / "media"),
            "STT_ENABLED": "true",
            "VISION_ENABLED": "false",
            "TTS_ENABLED": "false",
            "MIC_SILENCE_SECONDS": "0.2",
            "MIC_NO_SPEECH_TIMEOUT_S": "0.5",
            "MIC_MAX_SECONDS": "3",
        }
    )


def wait_until(qt_app, condition, timeout_s: float = 4.0) -> None:
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


def build(qt_app, settings, microphone=None, stt=None):
    microphone = microphone or FakeMicrophone()
    stt = stt or FakeStt()
    application = Application(settings, QtLogHandler(), microphone=microphone, stt=stt)
    return application, microphone, stt


@pytest.fixture
def voice_app(qt_app, voice_settings):
    application, microphone, stt = build(qt_app, voice_settings)
    wait_until(qt_app, lambda: application.voice.status is VoiceStatus.READY)
    yield application, microphone, stt
    stt.release.set()
    application.shutdown()
    application.window.deleteLater()


def state(application) -> ConversationState:
    return application.conversation.state.state


def test_spoken_question_is_transcribed_and_answered(qt_app, voice_app, voice_settings):
    application, microphone, stt = voice_app
    screen = application.window.user_screen
    seen = []
    application.conversation.state.state_changed.connect(lambda _old, new: seen.append(new))

    screen._on_talk_button()
    assert state(application) is ConversationState.LISTENING
    assert microphone.recording and microphone.started_with == [None]
    assert screen._talk_button.text() == "พูดจบแล้ว"

    microphone.level = LOUD
    pump(qt_app, 0.5)
    assert state(application) is ConversationState.LISTENING  # still speaking
    microphone.level = QUIET  # stops talking; the end is detected automatically
    wait_until(qt_app, lambda: state(application) is ConversationState.SPEAKING)

    assert not microphone.recording and microphone.stops == 1
    assert seen == [
        ConversationState.LISTENING, ConversationState.THINKING, ConversationState.SPEAKING,
    ]
    faq = voice_settings.faq[2]
    assert screen.transcript_texts() == [f"คุณ: {stt.text}", f"หุ่นยนต์: {faq.answer}"]
    assert screen._talk_button.text() == "เริ่มสนทนา"


def test_talk_button_ends_listening_early(qt_app, voice_app):
    application, microphone, stt = voice_app
    screen = application.window.user_screen
    screen._on_talk_button()
    microphone.level = LOUD
    pump(qt_app, 0.1)
    screen._on_talk_button()  # "I have finished speaking"
    wait_until(qt_app, lambda: state(application) is ConversationState.SPEAKING)
    assert stt.transcribed == 1


def test_silence_ends_the_turn_with_a_notice(qt_app, voice_app):
    application, microphone, stt = voice_app
    screen = application.window.user_screen
    screen._on_talk_button()
    wait_until(qt_app, lambda: state(application) is ConversationState.IDLE)
    assert not microphone.recording
    assert stt.transcribed == 0  # nothing was sent to the model
    assert screen._message_bar.text() == NO_SPEECH_MESSAGE
    assert screen.transcript_texts() == []


def test_microphone_with_no_signal_at_all_gets_its_own_message(qt_app, voice_app):
    application, microphone, _stt = voice_app
    microphone.peak = 0.0  # muted, or the OS is withholding microphone access
    screen = application.window.user_screen
    screen._on_talk_button()
    wait_until(qt_app, lambda: state(application) is ConversationState.IDLE)
    assert screen._message_bar.text() == DEAD_MICROPHONE_MESSAGE


def test_empty_transcription_is_treated_as_no_speech(qt_app, voice_app):
    application, microphone, stt = voice_app
    stt.text = ""
    screen = application.window.user_screen
    screen._on_talk_button()
    microphone.level = LOUD
    pump(qt_app, 0.4)
    microphone.level = QUIET
    wait_until(qt_app, lambda: stt.transcribed == 1 and state(application) is ConversationState.IDLE)
    assert screen._message_bar.text() == NO_SPEECH_MESSAGE
    assert screen.transcript_texts() == []


def test_stop_while_listening_discards_the_recording(qt_app, voice_app):
    application, microphone, stt = voice_app
    application.window.user_screen._on_talk_button()
    microphone.level = LOUD
    pump(qt_app, 0.1)
    application.conversation.stop()
    assert not microphone.recording and microphone.cancels == 1
    pump(qt_app, 0.5)
    assert stt.transcribed == 0
    assert state(application) is ConversationState.IDLE


def test_stop_during_transcription_drops_the_late_result(qt_app, voice_app):
    application, microphone, stt = voice_app
    screen = application.window.user_screen
    stt.release.clear()  # transcription hangs until released
    screen._on_talk_button()
    microphone.level = LOUD
    pump(qt_app, 0.1)
    screen._on_talk_button()
    assert state(application) is ConversationState.THINKING
    application.conversation.stop()
    stt.release.set()
    pump(qt_app, 0.5)
    # The stopped job is dropped as soon as it can be (it is not even counted as done), and its
    # late result never reaches the screen.
    assert stt.transcribed == 0
    assert state(application) is ConversationState.IDLE
    assert screen.transcript_texts() == []


def test_microphone_failure_is_reported_and_typing_still_works(qt_app, voice_app, voice_settings):
    application, microphone, _stt = voice_app
    screen = application.window.user_screen
    microphone.fail_to_open = True
    screen._on_talk_button()
    assert state(application) is ConversationState.ERROR
    assert "ไมโครโฟน" in screen._message_bar.text()

    application.conversation.ask(voice_settings.faq[0].question)
    wait_until(qt_app, lambda: state(application) is ConversationState.SPEAKING)


def test_transcription_failure_is_reported(qt_app, voice_app):
    application, microphone, stt = voice_app
    stt.transcribe_error = SpeechToTextError("ถอดเสียงเป็นข้อความไม่สำเร็จ")
    screen = application.window.user_screen
    screen._on_talk_button()
    microphone.level = LOUD
    pump(qt_app, 0.1)
    screen._on_talk_button()
    wait_until(qt_app, lambda: state(application) is ConversationState.ERROR)
    assert screen._message_bar.text() == STT_FAILED_MESSAGE
    assert screen.transcript_texts() == []


def test_saved_microphone_is_used_and_admin_test_shows_level(qt_app, voice_app):
    application, microphone, _stt = voice_app
    application.content.set_mic_device_name("USB Mic")
    application.window.user_screen._on_talk_button()
    assert microphone.started_with == ["USB Mic"]
    application.conversation.stop()

    levels = []
    application.voice.level_changed.connect(levels.append)
    application.voice.start_mic_test("Fake Mic")
    microphone.level = LOUD
    wait_until(qt_app, lambda: LOUD in levels)
    application.voice.stop_mic_test()
    assert not microphone.recording
    assert state(application) is ConversationState.IDLE  # a test is not a conversation turn


def test_model_load_failure_leaves_app_usable_by_typing(qt_app, voice_settings):
    stt = FakeStt()
    stt.load_error = SpeechToTextError("โหลดโมเดลถอดเสียงไม่สำเร็จ")
    application, microphone, _ = build(qt_app, voice_settings, stt=stt)
    try:
        wait_until(qt_app, lambda: application.voice.status is VoiceStatus.UNAVAILABLE)
        screen = application.window.user_screen
        assert "ใช้ไม่ได้" in screen._voice_chip.text()

        screen._on_talk_button()  # falls back to typed input
        assert state(application) is ConversationState.LISTENING
        assert not microphone.recording
        assert "โหลดโมเดลถอดเสียงไม่สำเร็จ" in screen._message_bar.text()
        application.conversation.ask(voice_settings.faq[0].question)
        wait_until(qt_app, lambda: state(application) is ConversationState.SPEAKING)
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_voice_disabled_never_opens_the_microphone(qt_app, settings):
    application, microphone, stt = build(qt_app, settings)  # STT_ENABLED=false
    try:
        assert application.voice.status is VoiceStatus.DISABLED
        application.window.user_screen._on_talk_button()
        assert state(application) is ConversationState.LISTENING
        assert microphone.started_with == []
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_after_an_answer_the_robot_listens_for_the_next_question(qt_app, voice_app):
    application, microphone, stt = voice_app
    screen = application.window.user_screen
    screen._on_talk_button()
    microphone.level = LOUD
    pump(qt_app, 0.4)
    microphone.level = QUIET
    wait_until(qt_app, lambda: state(application) is ConversationState.SPEAKING)
    assert microphone.started_with == [None]

    # The answer finishes -> the microphone opens again by itself.
    wait_until(qt_app, lambda: len(microphone.started_with) == 2, timeout_s=12.0)
    assert state(application) is ConversationState.LISTENING and microphone.recording

    stt.text = "เวลาทำการของวิทยาลัย"
    microphone.level = LOUD
    pump(qt_app, 0.4)
    microphone.level = QUIET
    wait_until(qt_app, lambda: len(screen.transcript_texts()) == 4)
    assert screen.transcript_texts()[2] == "คุณ: เวลาทำการของวิทยาลัย"

    # Third round: the visitor says nothing more. The robot just keeps listening (always on),
    # without a notice and without the transcript growing.
    wait_until(qt_app, lambda: len(microphone.started_with) >= 3, timeout_s=12.0)
    pump(qt_app, 1.0)
    assert state(application) is ConversationState.LISTENING and microphone.recording
    assert screen._message_bar.text() != NO_SPEECH_MESSAGE
    assert len(screen.transcript_texts()) == 4


def test_follow_up_listening_stops_when_the_visitor_goes_back_home(qt_app, voice_app):
    application, microphone, _stt = voice_app
    screen = application.window.user_screen
    screen._on_talk_button()
    microphone.level = LOUD
    pump(qt_app, 0.4)
    microphone.level = QUIET
    wait_until(qt_app, lambda: state(application) is ConversationState.SPEAKING)
    screen.reset_session()  # presses "home" while the robot is answering
    pump(qt_app, 1.0)
    assert microphone.started_with == [None]
    assert state(application) is ConversationState.IDLE


def test_follow_up_can_be_turned_off(qt_app, tmp_path):
    settings = load_settings(
        environ={
            "DATA_DIR": str(tmp_path / "data"), "LOG_DIR": str(tmp_path / "logs"),
            "MEDIA_DIR": str(tmp_path / "media"), "STT_ENABLED": "true",
            "VISION_ENABLED": "false", "VOICE_FOLLOW_UP": "false", "TTS_ENABLED": "false",
        }
    )
    application, microphone, _stt = build(qt_app, settings)
    try:
        wait_until(qt_app, lambda: application.voice.status is VoiceStatus.READY)
        finished = []
        application.conversation.turn_finished.connect(lambda: finished.append(True))
        application.window.user_screen.show_page(1)
        application.conversation.ask(settings.faq[0].question)
        wait_until(qt_app, lambda: finished, timeout_s=12.0)
        pump(qt_app, 0.3)
        assert microphone.started_with == []
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_saved_microphone_channel_is_used(qt_app, voice_app):
    application, microphone, _stt = voice_app
    application.content.save_audio_settings("USB Mic", 2, None)  # e.g. a microphone array's processed channel
    application.window.user_screen._on_talk_button()
    assert (microphone.started_with, microphone.channels) == (["USB Mic"], [2])


def test_ready_only_after_the_microphone_has_delivered_audio(qt_app, voice_app):
    application, microphone, _stt = voice_app
    assert microphone.probes == 1  # checked once the model had loaded
    assert application.voice.status_text == "พร้อม — ไมโครโฟน: Fake Mic"
    assert not microphone.recording  # the check does not leave it open


def test_failed_microphone_check_is_shown_and_retried_on_the_next_press(qt_app, voice_settings):
    microphone = FakeMicrophone()
    microphone.fail_to_open = True
    application, microphone, _stt = build(qt_app, voice_settings, microphone=microphone)
    try:
        wait_until(qt_app, lambda: application.voice.status is VoiceStatus.UNAVAILABLE)
        screen = application.window.user_screen
        assert "ใช้ไม่ได้" in screen._voice_chip.text()
        assert "เปิดไมโครโฟนไม่ได้" in application.voice.status_text

        microphone.fail_to_open = False  # plugged in, or allowed in Windows settings
        screen._on_talk_button()
        assert microphone.recording and state(application) is ConversationState.LISTENING
        assert application.voice.status is VoiceStatus.READY
    finally:
        application.shutdown()
        application.window.deleteLater()


def test_starting_twice_does_not_reopen_the_microphone(qt_app, voice_app):
    application, microphone, _stt = voice_app
    screen = application.window.user_screen
    screen._start_conversation()
    application.window.user_screen.listen_hands_free()  # the camera asks too
    application.voice.start_listening(follow_up=True)
    assert microphone.started_with == [None] and microphone.cancels == 0
    assert state(application) is ConversationState.LISTENING


def test_many_questions_in_a_row_without_restarting(qt_app, voice_app):
    application, microphone, stt = voice_app
    screen = application.window.user_screen
    for round_ in range(4):
        stt.text = ["สมัครเรียนได้ที่ไหน", "Where is the toilet?", "เวลาทำการของวิทยาลัย", "ขอบคุณครับ"][round_]
        screen._on_talk_button()
        microphone.level = LOUD
        pump(qt_app, 0.5)  # comfortably over the 0.25 s that counts as speech, even on a busy machine
        microphone.level = QUIET
        wait_until(qt_app, lambda: state(application) is ConversationState.SPEAKING)
        application.conversation.stop()  # "หยุดสนทนา" while it reads the answer
        assert not microphone.recording
    assert stt.transcribed == 4 and microphone.stops == 4
    assert len(screen.transcript_texts()) == 8


def test_admin_microphone_test_uses_exactly_the_chosen_device(qt_app, voice_app):
    application, microphone, _stt = voice_app
    application.voice.start_mic_test("USB Mic")
    application.voice.stop_mic_test()
    assert microphone.started_with[-1] == "USB Mic" and microphone.fallbacks[-1] is False


def test_english_mode_transcribes_as_english_until_the_session_ends(qt_app, voice_app):
    application, microphone, stt = voice_app
    screen = application.window.user_screen
    screen._toggle_language()
    assert screen._language_button.text() == "English"  # shows the language in use
    screen._on_talk_button()
    microphone.level = LOUD
    pump(qt_app, 0.1)
    screen._on_talk_button()
    wait_until(qt_app, lambda: stt.transcribed == 1)
    assert stt.languages == ["en"]

    screen.reset_session()  # the next visitor starts in Thai again
    assert application.voice.language is None and screen._language_button.text() == "ภาษาไทย"


# --- always listening: no "start" press for each question ---

def test_idle_robot_listens_again_by_itself_until_stopped(qt_app, voice_app):
    application, microphone, _stt = voice_app
    window = application.window
    window._listen_timer.setInterval(100)  # the suite keeps it off (conftest); this test wants it on
    window._listen_timer.start()
    wait_until(qt_app, lambda: state(application) is ConversationState.LISTENING)
    assert microphone.recording

    application.conversation.stop()  # whatever ended it, it is idle again ...
    wait_until(qt_app, lambda: state(application) is ConversationState.LISTENING)  # ... and listening

    window.user_screen._stop_conversation()  # "หยุดสนทนา": the microphone stays closed
    pump(qt_app, 1.0)
    assert state(application) is ConversationState.IDLE and not microphone.recording
