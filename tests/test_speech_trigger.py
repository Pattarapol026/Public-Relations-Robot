"""When the robot may speak, and that it stays silent once stopped.

The real application with real buttons; only the microphone, STT, AI, voices and
speaker are stand-ins. "Silent" means the speaker was never opened or fed.
"""
from __future__ import annotations

import threading
import time

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton

from app.application import Application
from app.speech_controller import SpeechStatus
from app.state import ConversationState
from app.voice_controller import VoiceStatus
from config.settings import load_settings
from fakes import LOUD, QUIET, FakeMicrophone, FakeStt
from test_speech import FakePlayback, FakeTts
from ui.admin_ai import AiTab
from ui.fonts import apply_app_font
from ui.log_bridge import QtLogHandler
from ui.user_screen import UserScreen

THAI_QUESTION = "เรื่องนี้ไม่มีในฐานความรู้แน่นอนครับ"
ENGLISH_QUESTION = "Tell me something that is certainly not in the knowledge base"
THAI_ANSWER = "คำตอบจากปัญญาประดิษฐ์ครับ"
ENGLISH_ANSWER = "This is the answer from the model."


class GatedTts(FakeTts):
    """A voice that can be held in the middle of making audio."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = threading.Event()
        self.gate.set()
        self.entered = threading.Event()

    def synthesize(self, text, speed=None, language=None):
        self.entered.set()
        self.gate.wait(5.0)
        return super().synthesize(text, speed, language)


class LongPlayback(FakePlayback):
    PIECE_SECONDS = 30.0  # still playing whenever the test presses stop


class FakeAi:
    """Answers in the language of the question; can be held while "thinking"."""

    model = "fake"

    def __init__(self) -> None:
        self.gate = threading.Event()
        self.gate.set()
        self.entered = threading.Event()

    def generate(self, _instruction, turns, max_output_tokens=400, temperature=0.3):
        self.entered.set()
        self.gate.wait(5.0)
        question = turns[-1].text
        return THAI_ANSWER if any("฀" <= c <= "๿" for c in question) else ENGLISH_ANSWER


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


class Robot:
    """The application under test and what it is plugged into."""

    def __init__(self, qt_app, tmp_path, playback=None, **environ) -> None:
        self.qt_app = qt_app
        self.microphone, self.stt = FakeMicrophone(), FakeStt(THAI_QUESTION)
        self.tts, self.playback, self.ai = GatedTts(), playback or FakePlayback(), FakeAi()
        settings = load_settings(environ={
            "DATA_DIR": str(tmp_path / "data"), "LOG_DIR": str(tmp_path / "logs"),
            "MEDIA_DIR": str(tmp_path / "media"), "STT_ENABLED": "true", "TTS_ENABLED": "true",
            "VISION_ENABLED": "false", "MIC_SILENCE_SECONDS": "0.1", "MIC_NO_SPEECH_TIMEOUT_S": "0.8",
            "MIC_MAX_SECONDS": "3", "VOICE_FOLLOW_UP": "false", **environ,
        })
        self.app = Application(
            settings, QtLogHandler(), microphone=self.microphone, stt=self.stt,
            tts=self.tts, playback=self.playback, gemini=self.ai,
        )
        self.screen = self.app.window.user_screen
        wait_until(qt_app, lambda: self.app.voice.status is VoiceStatus.READY)
        wait_until(qt_app, lambda: self.app.speech.status is SpeechStatus.READY)

    def close(self) -> None:
        self.tts.gate.set()
        self.ai.gate.set()
        self.stt.release.set()
        self.app.shutdown()
        self.app.window.deleteLater()

    @property
    def state(self) -> ConversationState:
        return self.app.conversation.state.state

    def press(self, text: str) -> None:
        """Click the button on the visitor screen with this label, as a visitor would."""
        buttons = [b for b in self.screen.findChildren(QPushButton) if b.text() == text]
        assert buttons, f"no button {text!r}"
        buttons[0].click()

    def say_into_microphone(self, words: str | None = None) -> None:
        if words is not None:
            self.stt.text = words
        self.microphone.level = LOUD
        pump(self.qt_app, 0.35)  # shorter than min_speech_s (0.25 s) would count as a cough
        self.microphone.level = QUIET

    def start_and_say(self, words: str) -> None:
        self.screen.show_page(UserScreen.PAGE_CHAT)
        self.screen._on_talk_button()
        assert self.state is ConversationState.LISTENING
        self.say_into_microphone(words)

    def ask_and_hear(self, words: str) -> None:
        before = self.playback.fed
        self.start_and_say(words)
        wait_until(self.qt_app, lambda: self.playback.fed > before)

    def assert_never_spoke(self) -> None:
        assert self.playback.started_with == [] and self.playback.fed == 0 and self.tts.spoken == []

    def answers(self) -> list[str]:
        return [t for t in self.screen.transcript_texts() if t.startswith("หุ่นยนต์: ")]


@pytest.fixture
def robot(qt_app, tmp_path):
    robot = Robot(qt_app, tmp_path)
    yield robot
    robot.close()


@pytest.fixture
def playing_robot(qt_app, tmp_path):
    robot = Robot(qt_app, tmp_path, playback=LongPlayback())
    yield robot
    robot.close()


# --- language ---


def test_1_typed_hello_is_answered_in_english_on_screen_and_aloud(qt_app, robot):
    robot.screen._input.setText("Hello")
    robot.screen._send_typed_question()
    wait_until(qt_app, lambda: robot.answers())
    assert robot.answers() == ["หุ่นยนต์: Hello! How can I help you today?"]
    wait_until(qt_app, lambda: robot.playback.fed == 2)  # one piece for each sentence
    assert robot.tts.spoken == ["Hello!", "How can I help you today?"]


def test_1b_typed_hello_after_thai_is_still_english(qt_app, robot):
    robot.app.conversation.ask(THAI_QUESTION)
    wait_until(qt_app, lambda: len(robot.answers()) == 1)
    robot.app.conversation.ask("Hello")
    wait_until(qt_app, lambda: len(robot.answers()) == 2)
    assert robot.answers()[-1] == "หุ่นยนต์: Hello! How can I help you today?"


def test_2_spoken_english_is_answered_in_english_with_the_english_voice(qt_app, robot):
    robot.ask_and_hear(ENGLISH_QUESTION)
    assert robot.answers() == [f"หุ่นยนต์: {ENGLISH_ANSWER}"]
    assert robot.tts.spoken == [ENGLISH_ANSWER] and robot.tts.languages == ["en"]


def test_2b_spoken_hello_is_answered_in_english(qt_app, robot):
    robot.ask_and_hear("Hello")
    assert robot.tts.spoken and set(robot.tts.languages) == {"en"}


def test_3_spoken_thai_is_answered_in_thai_with_the_thai_voice(qt_app, robot):
    robot.ask_and_hear(THAI_QUESTION)
    assert robot.answers() == [f"หุ่นยนต์: {THAI_ANSWER}"]
    assert robot.tts.spoken == [THAI_ANSWER] and robot.tts.languages == ["th"]


def test_4_alternating_languages_changes_answer_and_voice_each_time(qt_app, robot):
    for words, answer, language in (
        (THAI_QUESTION, THAI_ANSWER, "th"), (ENGLISH_QUESTION, ENGLISH_ANSWER, "en"),
        (THAI_QUESTION + " อีกครั้ง", THAI_ANSWER, "th"), (ENGLISH_QUESTION + " again", ENGLISH_ANSWER, "en"),
    ):
        robot.ask_and_hear(words)
        assert robot.answers()[-1] == f"หุ่นยนต์: {answer}"
        assert robot.tts.languages[-1] == language
        wait_until(qt_app, lambda: not robot.app.speech.is_speaking or robot.playback.is_active)
        robot.app.conversation.stop()


def test_a_spoken_command_in_english_is_replied_to_in_english(qt_app, robot):
    robot.ask_and_hear("stop")
    assert robot.answers() == ["หุ่นยนต์: Stopped."] and robot.tts.languages == ["en"]


# --- the language button ---


def test_the_language_button_follows_the_language_in_use_by_itself(qt_app, robot):
    button = robot.screen._language_button
    assert button.text() == "ภาษาไทย"
    robot.ask_and_hear(ENGLISH_QUESTION)
    assert button.text() == "English"
    robot.ask_and_hear(THAI_QUESTION)
    assert button.text() == "ภาษาไทย"


def test_the_language_button_follows_typed_questions_and_english_commands(qt_app, robot):
    button = robot.screen._language_button
    robot.app.conversation.ask("Hello")
    wait_until(qt_app, lambda: button.text() == "English")
    robot.app.conversation.ask(THAI_QUESTION)
    wait_until(qt_app, lambda: button.text() == "ภาษาไทย")
    robot.ask_and_hear("stop")
    assert button.text() == "English"


def test_following_english_does_not_lock_the_microphone_to_english(qt_app, robot):
    robot.ask_and_hear(ENGLISH_QUESTION)
    assert robot.screen._language_button.text() == "English"
    assert robot.app.voice.language is None  # Thai can still be understood next
    robot.ask_and_hear(THAI_QUESTION)
    assert robot.stt.languages == [None, None]


def test_pressing_the_button_after_it_followed_english_goes_to_thai(qt_app, robot):
    robot.ask_and_hear(ENGLISH_QUESTION)
    robot.press("English")
    assert robot.screen._language_button.text() == "ภาษาไทย" and robot.app.voice.language is None
    robot.press("ภาษาไทย")
    assert robot.screen._language_button.text() == "English" and robot.app.voice.language == "en"


def test_a_thai_answer_releases_english_mode_set_by_hand(qt_app, robot):
    robot.press("ภาษาไทย")
    assert robot.app.voice.language == "en"
    robot.app.conversation.ask(THAI_QUESTION)
    wait_until(qt_app, lambda: robot.screen._language_button.text() == "ภาษาไทย")
    assert robot.app.voice.language is None


# --- when the robot may not speak ---


def test_startup_is_silent(qt_app, robot):
    pump(qt_app, 0.5)
    robot.assert_never_spoke()
    assert robot.microphone.started_with == []


def test_5_opening_the_dashboard_is_silent(qt_app, robot):
    robot.app.window.enter_admin()
    pump(qt_app, 0.5)
    robot.assert_never_spoke()
    assert robot.microphone.started_with == []


def test_6_asking_the_ai_in_the_admin_shows_text_and_is_silent(qt_app, robot):
    robot.app.window.enter_admin()
    tab = robot.app.window.findChild(AiTab)
    field = tab.findChildren(QLineEdit)[0]
    field.setText(ENGLISH_QUESTION)
    tab._ask()
    wait_until(qt_app, lambda: robot.ai.entered.is_set())
    pump(qt_app, 0.5)
    robot.assert_never_spoke()
    assert robot.state is ConversationState.IDLE


def test_7_opening_the_robot_screen_without_pressing_start_is_silent(qt_app, robot):
    pump(qt_app, 0.5)
    assert robot.screen.current_page() == UserScreen.PAGE_CHAT
    robot.assert_never_spoke()
    assert robot.microphone.started_with == [] and not robot.app.conversation.session_active


def test_typed_question_on_the_robot_screen_is_answered_aloud_too(qt_app, robot):
    """Since 2026-11-06 an answer is spoken however the question came; before, only spoken questions were."""
    robot.screen._input.setText(ENGLISH_QUESTION)
    robot.screen._send_typed_question()
    wait_until(qt_app, lambda: len(robot.answers()) == 1)
    wait_until(qt_app, lambda: robot.playback.fed == 1)
    assert robot.tts.spoken == [ENGLISH_ANSWER]


def test_the_robot_screen_has_no_popular_questions_menu(qt_app, robot):
    texts = {button.text() for button in robot.screen.findChildren(QPushButton)}
    assert not texts & set(robot.app.content.shortcut_questions())


def test_a_typed_question_within_a_session_is_answered_aloud_and_closes_the_microphone(qt_app, robot):
    robot.screen._on_talk_button()
    assert robot.microphone.recording
    robot.screen._input.setText(ENGLISH_QUESTION)
    robot.screen._send_typed_question()
    wait_until(qt_app, lambda: robot.answers())
    wait_until(qt_app, lambda: robot.playback.fed == 1)
    assert robot.tts.spoken == [ENGLISH_ANSWER]
    assert not robot.microphone.recording  # the robot's own voice is not transcribed


def test_a_typed_answer_does_not_open_the_microphone_afterwards(qt_app, tmp_path):
    robot = Robot(qt_app, tmp_path, VOICE_FOLLOW_UP="true")
    try:
        finished = []
        robot.app.conversation.turn_finished.connect(finished.append)
        robot.app.conversation.ask("Hello")
        wait_until(qt_app, lambda: finished, timeout_s=8.0)
        pump(qt_app, 0.2)
        assert robot.microphone.started_with == []
    finally:
        robot.close()


def test_a_greeting_is_spoken(qt_app, robot):
    robot.screen.greet("สวัสดีครับ ยินดีต้อนรับ")
    assert robot.answers() == ["หุ่นยนต์: สวัสดีครับ ยินดีต้อนรับ"]
    wait_until(qt_app, lambda: robot.playback.fed == 1)
    assert robot.tts.spoken == ["สวัสดีครับ ยินดีต้อนรับ"]


def test_a_greeting_can_be_shown_without_a_voice(qt_app, robot):
    """The screen cleared again with nobody using it: with no camera there is no telling anyone is there."""
    robot.screen.greet("สวัสดีครับ ยินดีต้อนรับ", aloud=False)
    assert robot.answers() == ["หุ่นยนต์: สวัสดีครับ ยินดีต้อนรับ"]
    pump(qt_app, 0.3)
    robot.assert_never_spoke()


@pytest.mark.parametrize("event", ["sign", "announcement", "farewell"])
def test_events_that_are_not_answers_are_shown_in_silence(qt_app, robot, event):
    if event == "sign":
        robot.screen.show_sign_response("บัตรแผนที่", "ห้องสมุดอยู่ชั้นสองครับ")
        assert robot.answers() == ["หุ่นยนต์: ห้องสมุดอยู่ชั้นสองครับ"]
    elif event == "announcement":
        robot.screen.announce("ประกาศทดสอบ", None)
        assert robot.screen._message_bar.text() == "ประกาศทดสอบ"
    else:
        robot.app.conversation.say("ขอบคุณครับ")
    assert robot.state is ConversationState.SPEAKING  # the turn goes on, without sound
    pump(qt_app, 0.3)
    robot.assert_never_spoke()


def test_the_cameras_hands_free_listening_is_answered_aloud_like_pressing_start(qt_app, robot):
    robot.screen.show_page(UserScreen.PAGE_CHAT)
    robot.screen.listen_hands_free()
    assert robot.microphone.recording and robot.app.conversation.session_active
    robot.say_into_microphone(ENGLISH_QUESTION)
    wait_until(qt_app, lambda: robot.playback.fed == 1)
    assert robot.answers() == [f"หุ่นยนต์: {ENGLISH_ANSWER}"]
    assert robot.tts.spoken == [ENGLISH_ANSWER]


# --- the cases when it may ---


def test_8_pressing_start_and_speaking_is_answered_aloud(qt_app, robot):
    robot.press("เริ่มสนทนา")
    assert robot.microphone.recording and robot.app.conversation.session_active
    robot.say_into_microphone(ENGLISH_QUESTION)
    wait_until(qt_app, lambda: robot.playback.fed == 1)
    assert robot.tts.spoken == [ENGLISH_ANSWER]


def test_8b_the_start_button_on_the_home_page_counts(qt_app, robot):
    robot.press("เริ่มสนทนา")
    assert robot.screen.current_page() == UserScreen.PAGE_CHAT and robot.microphone.recording
    robot.say_into_microphone(ENGLISH_QUESTION)
    wait_until(qt_app, lambda: robot.playback.fed == 1)


def test_follow_up_questions_in_the_session_are_answered_aloud(qt_app, tmp_path):
    robot = Robot(qt_app, tmp_path, VOICE_FOLLOW_UP="true")
    try:
        robot.ask_and_hear(ENGLISH_QUESTION)
        wait_until(qt_app, lambda: robot.microphone.recording)  # listening again by itself
        robot.say_into_microphone(THAI_QUESTION)
        wait_until(qt_app, lambda: robot.playback.fed == 2)
        assert robot.tts.languages == ["en", "th"]
    finally:
        robot.close()


def test_9_silence_after_pressing_start_gets_no_spoken_answer(qt_app, robot):
    robot.press("เริ่มสนทนา")
    wait_until(qt_app, lambda: not robot.microphone.recording)  # no speech within the timeout
    pump(qt_app, 0.3)
    robot.assert_never_spoke()
    assert not robot.app.conversation.session_active and robot.answers() == []


def test_9b_noise_that_transcribes_to_nothing_gets_no_spoken_answer(qt_app, robot):
    robot.start_and_say("")
    wait_until(qt_app, lambda: robot.state is ConversationState.IDLE)
    pump(qt_app, 0.3)
    robot.assert_never_spoke()
    assert not robot.app.conversation.session_active


def test_silence_during_follow_up_keeps_listening(qt_app, tmp_path):
    """Always listening: nobody speaking after an answer does not end the session or close the microphone."""
    robot = Robot(qt_app, tmp_path, VOICE_FOLLOW_UP="true")
    try:
        robot.ask_and_hear(ENGLISH_QUESTION)
        wait_until(qt_app, lambda: robot.microphone.recording)
        pump(qt_app, 2.0)  # well past the 0.8 s no-speech timeout
        assert robot.microphone.recording and robot.app.conversation.session_active
        spoken = list(robot.tts.spoken)
        robot.say_into_microphone(THAI_QUESTION)  # the next question needs no button press
        wait_until(qt_app, lambda: len(robot.answers()) == 2)
        wait_until(qt_app, lambda: len(robot.tts.spoken) == len(spoken) + 1)
        assert robot.tts.spoken == spoken + [THAI_ANSWER]
    finally:
        robot.close()


# --- stop ---


@pytest.mark.parametrize("button", ["หยุดสนทนา", "หยุดหุ่นยนต์"])
def test_10_stop_silences_the_speaker_at_once(qt_app, playing_robot, button):
    robot = playing_robot
    robot.ask_and_hear(ENGLISH_QUESTION)
    assert robot.playback.is_active and robot.app.speech.is_speaking
    stops = robot.playback.stops
    robot.press(button)
    # In the same instant, without returning to the event loop:
    assert robot.playback.stops == stops + 1 and not robot.playback.is_active
    assert not robot.app.speech.is_speaking and not robot.app.conversation.session_active
    assert robot.state is ConversationState.IDLE


def test_11_nothing_follows_for_five_seconds_after_stop(qt_app, playing_robot):
    robot = playing_robot
    robot.stt.text = ENGLISH_QUESTION + ". Second sentence here. Third sentence here."
    robot.tts.gate.set()
    robot.ask_and_hear(robot.stt.text)
    finished = []
    robot.app.conversation.turn_finished.connect(finished.append)
    robot.press("หยุดสนทนา")
    fed, spoken, opened = robot.playback.fed, list(robot.tts.spoken), list(robot.playback.started_with)
    pump(qt_app, 5.5)
    assert robot.playback.fed == fed and robot.tts.spoken == spoken
    assert robot.playback.started_with == opened and finished == [] and not robot.microphone.recording


def test_11b_after_stop_nothing_opens_the_microphone_until_start(qt_app, tmp_path):
    """The camera (someone close), a greeting's end or a follow-up used to open it again,
    and talk still going on nearby was answered."""
    robot = Robot(qt_app, tmp_path, VOICE_FOLLOW_UP="true")
    try:
        robot.ask_and_hear(ENGLISH_QUESTION)
        robot.press("หยุดสนทนา")
        assert robot.screen.listening_paused and not robot.microphone.recording
        robot.screen.listen_hands_free()  # what the camera does for someone at conversation distance
        robot.screen.listen_hands_free(quiet=True)  # what the end of a greeting does
        robot.screen.listen_for_follow_up()
        pump(qt_app, 0.3)
        assert not robot.microphone.recording and not robot.app.conversation.session_active
        assert robot.state is ConversationState.IDLE

        robot.press("เริ่มสนทนา")  # only this listens again, and hands-free works as before
        assert robot.microphone.recording and not robot.screen.listening_paused
        robot.say_into_microphone(THAI_QUESTION)
        wait_until(qt_app, lambda: robot.tts.spoken[-1:] == [THAI_ANSWER])
    finally:
        robot.close()


def test_12_clearing_for_the_next_visitor_silences_and_stays_on_the_conversation(qt_app, playing_robot):
    robot = playing_robot
    robot.ask_and_hear(ENGLISH_QUESTION)
    robot.screen.reset_session()  # there is no home button any more; this is what idle time does
    assert not robot.playback.is_active and not robot.app.conversation.session_active
    assert robot.screen.current_page() == UserScreen.PAGE_CHAT and robot.screen.transcript_texts() == []
    fed = robot.playback.fed
    pump(qt_app, 0.5)
    assert robot.playback.fed == fed


@pytest.mark.parametrize("leave", ["window closed", "admin", "visitor gone"])
def test_12b_every_way_of_leaving_silences_at_once(qt_app, playing_robot, leave):
    robot = playing_robot
    window = robot.app.window
    robot.ask_and_hear(ENGLISH_QUESTION)
    assert robot.playback.is_active
    if leave == "window closed":
        window.close()
    elif leave == "admin":
        window.enter_admin()
    else:
        robot.screen.reset_session()
    assert not robot.playback.is_active
    assert not robot.app.speech.is_speaking and not robot.app.conversation.session_active


def test_12c_leaving_while_listening_closes_the_microphone(qt_app, robot):
    robot.press("เริ่มสนทนา")
    assert robot.microphone.recording
    robot.press("หยุดสนทนา")
    assert not robot.microphone.recording and robot.state is ConversationState.IDLE
    pump(qt_app, 0.5)
    robot.assert_never_spoke()


def test_13_a_new_session_hears_nothing_of_the_one_before(qt_app, robot):
    robot.ai.gate.clear()
    robot.start_and_say(ENGLISH_QUESTION)
    wait_until(qt_app, lambda: robot.ai.entered.is_set())
    robot.press("หยุดสนทนา")

    robot.ai.gate.set()  # the old answer arrives; a new session is open by now
    robot.ask_and_hear(THAI_QUESTION)
    pump(qt_app, 0.5)
    assert robot.tts.spoken == [THAI_ANSWER] and robot.answers() == [f"หุ่นยนต์: {THAI_ANSWER}"]


def test_13b_a_new_session_started_while_the_old_answer_is_still_thinking(qt_app, robot):
    robot.ai.gate.clear()
    robot.start_and_say(ENGLISH_QUESTION)
    wait_until(qt_app, lambda: robot.ai.entered.is_set())
    robot.press("เริ่มสนทนา")  # started over while the first answer is still being made
    assert robot.microphone.recording
    robot.ai.gate.set()
    pump(qt_app, 0.5)
    robot.assert_never_spoke()
    assert robot.answers() == [] and robot.state is ConversationState.LISTENING


def test_14a_stop_during_speech_recognition(qt_app, robot):
    robot.stt.release.clear()
    robot.start_and_say(ENGLISH_QUESTION)
    wait_until(qt_app, lambda: robot.state is ConversationState.THINKING)
    robot.press("หยุดสนทนา")
    robot.stt.release.set()
    pump(qt_app, 0.5)
    robot.assert_never_spoke()
    assert robot.screen.transcript_texts() == []  # the question itself is dropped too


def test_14b_stop_while_the_ai_is_making_the_answer(qt_app, robot):
    robot.ai.gate.clear()
    robot.start_and_say(ENGLISH_QUESTION)
    wait_until(qt_app, lambda: robot.ai.entered.is_set())
    robot.press("หยุดสนทนา")
    robot.ai.gate.set()
    pump(qt_app, 0.5)
    robot.assert_never_spoke()
    assert robot.answers() == [] and robot.state is ConversationState.IDLE


def test_14c_stop_while_the_voice_is_making_the_audio(qt_app, robot):
    robot.tts.gate.clear()
    robot.start_and_say(ENGLISH_QUESTION)
    wait_until(qt_app, lambda: robot.tts.entered.is_set())
    robot.press("หยุดสนทนา")
    robot.tts.gate.set()  # the audio is ready, but nobody wants it any more
    pump(qt_app, 0.5)
    assert robot.playback.fed == 0  # the speaker got nothing
    assert not robot.app.speech.is_speaking


def test_14d_stop_between_the_pieces_of_a_long_answer(qt_app, playing_robot):
    robot = playing_robot
    robot.ai.generate = lambda *_args, **_kwargs: "First sentence here. Second sentence here. Third sentence here."
    robot.ask_and_hear(ENGLISH_QUESTION)
    robot.tts.gate.clear()  # the next piece is being made
    robot.press("หยุดสนทนา")
    fed = robot.playback.fed
    robot.tts.gate.set()
    pump(qt_app, 0.5)
    assert robot.playback.fed == fed and fed < 3


def test_an_answer_never_plays_over_another(qt_app, playing_robot):
    robot = playing_robot
    robot.ask_and_hear(ENGLISH_QUESTION)
    opened, stops = len(robot.playback.started_with), robot.playback.stops
    robot.ask_and_hear(THAI_QUESTION)  # a new start cuts the answer being spoken
    assert len(robot.playback.started_with) == opened + 1 and robot.playback.stops >= stops + 1
    assert robot.tts.spoken[-1] == THAI_ANSWER


def test_a_speech_preview_in_the_admin_stops_on_leaving_it(qt_app, playing_robot):
    robot = playing_robot
    window = robot.app.window
    window.enter_admin()
    assert robot.app.speech.speak("ทดสอบเสียงพูด")
    wait_until(qt_app, lambda: robot.playback.fed == 1)
    window.leave_admin()
    assert not robot.playback.is_active and not robot.app.speech.is_speaking


def test_session_ids_differ_so_a_late_result_cannot_match(robot):
    conversation = robot.app.conversation
    conversation.begin_session()
    first = conversation._session_id
    conversation.end_session()
    conversation.begin_session()
    assert conversation._session_id > first + 1 and conversation.session_active
