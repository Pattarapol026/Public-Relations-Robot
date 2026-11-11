"""The robot's face: every expression draws, it animates between them, and it follows what the robot does."""
from __future__ import annotations

import time

import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from app.application import Application
from config.settings import load_settings
from ui.fonts import apply_app_font
from ui.log_bridge import QtLogHandler
from ui.robot_face import (
    MOOD_CONFUSED,
    MOOD_DELIGHTED,
    MOOD_HAPPY,
    MOOD_IDLE,
    MOOD_NEUTRAL,
    MOOD_SPEAKING,
    MOOD_THINKING,
    MOOD_WORRIED,
    MOODS,
    RobotFace,
)
from ui.user_screen import FACE_CAPTIONS
from vision.mock_source import MockVisionSource


@pytest.fixture(scope="module")
def qt_app():
    qt_app = QApplication.instance() or QApplication([])
    apply_app_font(qt_app)
    return qt_app


def pump_until(qt_app, condition, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while not condition():
        if time.monotonic() > deadline:
            return False
        qt_app.processEvents()
        time.sleep(0.01)
    return True


@pytest.mark.parametrize("mood", MOODS)
def test_every_expression_draws_something(qt_app, mood):
    face = RobotFace()
    face.resize(400, 300)
    face._mood, face._progress = mood, 1.0
    image = face.grab().toImage()
    background = image.pixelColor(5, image.height() // 2)
    centre_row = [image.pixelColor(x, y) for x in range(0, 400, 4) for y in range(60, 260, 4)]
    assert any(color != background for color in centre_row)  # eyes and mouth are on it


def test_a_change_of_expression_is_animated_then_settles(qt_app):
    face = RobotFace()
    face.resize(400, 300)
    face.set_mood(MOOD_HAPPY)
    assert face.mood == MOOD_HAPPY and face._previous == MOOD_IDLE and face._progress < 1.0
    face.grab()  # mid-change: both faces drawn, no error
    assert pump_until(qt_app, lambda: face._previous is None)
    assert face._progress == 1.0
    face.set_mood(MOOD_HAPPY)  # the same again: nothing to animate
    assert face._previous is None
    with pytest.raises(ValueError):
        face.set_mood("angry")


def test_the_face_only_animates_while_on_screen(qt_app):
    face = RobotFace()
    assert not face._ticker.isActive()
    face.show()
    assert face._ticker.isActive()
    face.hide()
    assert not face._ticker.isActive()


@pytest.fixture
def application(qt_app, tmp_path):
    settings = load_settings(environ={
        "DATA_DIR": str(tmp_path / "data"), "LOG_DIR": str(tmp_path / "logs"),
        "MEDIA_DIR": str(tmp_path / "media"), "STT_ENABLED": "false", "TTS_ENABLED": "false",
        "VISION_DWELL_SECONDS": "0.1", "VISION_GREET_DWELL_SECONDS": "0.1", "VISION_GREET_STILL_SECONDS": "0", "VISION_LEAVE_SECONDS": "0.4",
    })
    camera = MockVisionSource()
    application = Application(settings, QtLogHandler(), vision_source=camera)
    application.camera = camera
    yield application
    application.shutdown()
    application.window.deleteLater()


def test_the_face_sits_above_the_camera_and_the_robot_buttons_stay_at_the_bottom(qt_app, application):
    screen = application.window.user_screen
    layout = screen._face.parentWidget().layout()
    order = [layout.itemAt(i).widget() for i in range(layout.count())]
    assert order == [screen._face, screen._face_caption, screen._chat_camera]
    assert layout.stretch(2) == layout.stretch(0) > 0  # face and camera share the height equally
    # The robot's buttons are in the bottom bar, beside the stop button, on every page.
    buttons = {b.text() for b in screen._command_bar.findChildren(QPushButton)}
    assert buttons == {"◀  หันซ้าย", "หันขวา  ▶", "▲  เดินหน้า", "▼  ถอยหลัง"}
    # Without a camera picture the face takes the room the camera would have had.
    assert not screen._chat_camera.isVisibleTo(screen)
    assert layout.stretch(0) > 0


def test_idle_when_nobody_is_there_and_a_greeting_when_someone_arrives(qt_app, application):
    screen = application.window.user_screen
    assert screen.face_mood() == MOOD_IDLE
    assert screen._face_caption.text() == FACE_CAPTIONS[MOOD_IDLE]
    application.camera.place_person(2.0)
    assert pump_until(qt_app, lambda: screen.face_mood() == MOOD_HAPPY)
    screen._mood_timer.setInterval(50)
    screen._mood_timer.start()  # instead of waiting for the greeting to pass
    assert pump_until(qt_app, lambda: screen.face_mood() == MOOD_NEUTRAL)  # someone is there


def test_the_face_thinks_then_speaks_when_a_question_is_answered(qt_app, application):
    screen = application.window.user_screen
    seen = []
    application.conversation.state.state_changed.connect(lambda _old, _new: seen.append(screen.face_mood()))
    application.conversation.ask(application.settings.faq[0].question)
    assert pump_until(qt_app, lambda: MOOD_SPEAKING in seen)
    assert seen.index(MOOD_THINKING) < seen.index(MOOD_SPEAKING)


def test_no_answer_looks_confused_and_problems_look_worried(qt_app, application):
    screen = application.window.user_screen
    application.conversation.ask("ซฟหกดเ่าสวง")  # nothing in the knowledge base, no AI in the test
    assert pump_until(qt_app, lambda: screen.face_mood() == MOOD_CONFUSED)
    screen.notify("หยุดฉุกเฉิน", "error")
    assert screen.face_mood() == MOOD_WORRIED
    screen.feedback_recorded(True)
    assert screen.face_mood() == MOOD_DELIGHTED
    screen.reset_session()  # the visitor is gone: the moment does not linger
    assert screen.face_mood() == MOOD_IDLE
